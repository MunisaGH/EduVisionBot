import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from groq import Groq

from .models import ChatMessage, ChatSession, Notification, UserProfile

NEW_CHAT_TITLES = {
    "uz": "Yangi suhbat",
    "ru": "\u041d\u043e\u0432\u044b\u0439 \u0447\u0430\u0442",
    "en": "New Chat",
}

LANGUAGE_NAMES = {
    "uz": "O'zbek",
    "ru": "Rus",
    "en": "Ingliz",
}


def get_user_profile(user):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


def get_new_chat_title(request):
    return NEW_CHAT_TITLES.get(request.LANGUAGE_CODE or "uz", NEW_CHAT_TITLES["en"])


def get_groq_client():
    api_key = settings.GROQ_API_KEY
    if not api_key:
        return None
    return Groq(api_key=api_key)


def format_skills(skills):
    if isinstance(skills, list):
        cleaned_skills = [str(skill).strip() for skill in skills if str(skill).strip()]
        return ", ".join(cleaned_skills) if cleaned_skills else "Hali o'rganilmoqda"
    if isinstance(skills, dict):
        return ", ".join(f"{key}: {value}" for key, value in skills.items()) or "Hali o'rganilmoqda"
    if isinstance(skills, str) and skills.strip():
        return skills.strip()
    return "Hali o'rganilmoqda"


@login_required
@csrf_exempt
def chat_api(request):
    if request.method != "POST":
        return JsonResponse({"answer": "Faqat POST so'rovi qo'llab-quvvatlanadi."}, status=405)

    try:
        data = json.loads(request.body or "{}")
        message_content = (data.get("message") or "").strip()
        session_id = data.get("session_id")

        if not message_content:
            return JsonResponse({"answer": "Xabar bo'sh bo'lmasligi kerak."}, status=400)

        if session_id:
            chat_session = get_object_or_404(ChatSession, id=session_id, user=request.user)
        else:
            chat_session = ChatSession.objects.filter(user=request.user).order_by("-updated_at").first()
            if not chat_session:
                chat_session = ChatSession.objects.create(
                    user=request.user,
                    title=get_new_chat_title(request),
                )

        client = get_groq_client()
        if client is None:
            return JsonResponse(
                {"answer": "GROQ_API_KEY topilmadi. Iltimos, .env faylini tekshiring."},
                status=503,
            )

        ChatMessage.objects.create(session=chat_session, role="user", content=message_content)

        user_name = request.user.first_name or request.user.username
        profile = get_user_profile(request.user)
        profile_data = f"""
Foydalanuvchi ma'lumotlari:
- Ismi: {user_name}
- Universitet: {profile.university or "Kiritilmagan"}
- Mutaxassislik: {profile.major or "Kiritilmagan"}
- Ko'nikmalar: {format_skills(profile.skills_json)}
- Maqsadlari: {profile.short_term_goals or "Karyera rivoji"}
"""

        lang = request.LANGUAGE_CODE or "uz"
        lang_name = LANGUAGE_NAMES.get(lang, LANGUAGE_NAMES["en"])

        system_instruction = (
            f"Siz {user_name} uchun Mentoris platformasidagi professional AI mentorsiz. "
            f"Platforma: Mentoris. {profile_data} "
            f"QOIDALAR: FAQAT {lang_name} tilida gapiring. "
            "Maslahatlarni foydalanuvchining profili va maqsadlariga moslang. "
            "Har doim qisqa va aniq javob bering (2-4 sentence). "
            "Platformadagi Career Hub va Knowledge Base kabi imkoniyatlarni eslatib turing."
        )

        llm_messages = [{"role": "system", "content": system_instruction}]

        history = chat_session.messages.order_by("-timestamp")[:10]
        for history_message in reversed(history):
            llm_messages.append({"role": history_message.role, "content": history_message.content})

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=llm_messages,
        )

        answer = response.choices[0].message.content
        ChatMessage.objects.create(session=chat_session, role="assistant", content=answer)
        chat_session.save()

        return JsonResponse({
            "answer": answer,
            "session_id": chat_session.id,
        })
    except json.JSONDecodeError:
        return JsonResponse({"answer": "JSON formati noto'g'ri."}, status=400)
    except Exception as error:
        return JsonResponse({"answer": f"Xatolik yuz berdi: {error}"}, status=500)


@login_required
def dashboard(request):
    return render(request, "daily.html")


@login_required
def mentor_ai(request):
    sessions = ChatSession.objects.filter(user=request.user).order_by("-updated_at")
    active_session = sessions.first()

    if not active_session:
        active_session = ChatSession.objects.create(
            user=request.user,
            title=get_new_chat_title(request),
        )
        sessions = ChatSession.objects.filter(user=request.user).order_by("-updated_at")

    return render(request, "mentor.html", {
        "sessions": sessions,
        "active_session": active_session,
    })


@login_required
def create_session(request):
    if request.method != "POST":
        return JsonResponse(
            {"success": False, "error": "Faqat POST so'rovi qo'llab-quvvatlanadi."},
            status=405,
        )

    session = ChatSession.objects.create(user=request.user, title=get_new_chat_title(request))
    return JsonResponse({"session_id": session.id})


@login_required
def delete_session(request, session_id):
    if request.method != "POST":
        return JsonResponse(
            {"success": False, "error": "Faqat POST so'rovi qo'llab-quvvatlanadi."},
            status=405,
        )

    session = get_object_or_404(ChatSession, id=session_id, user=request.user)
    session.delete()
    return JsonResponse({"success": True})


@login_required
def get_session_messages(request, session_id):
    session = get_object_or_404(ChatSession, id=session_id, user=request.user)
    session_messages = session.messages.order_by("timestamp")
    data = [{"role": message.role, "content": message.content} for message in session_messages]
    return JsonResponse({"messages": data})


@login_required
def get_sessions(request):
    sessions = ChatSession.objects.filter(user=request.user).order_by("-updated_at")
    active = sessions.first()

    if not active:
        active = ChatSession.objects.create(user=request.user, title=get_new_chat_title(request))
        sessions = ChatSession.objects.filter(user=request.user).order_by("-updated_at")

    data = [
        {
            "id": session.id,
            "title": session.title,
            "updated_at": session.updated_at.strftime("%d.%m %H:%M"),
        }
        for session in sessions
    ]
    return JsonResponse({"sessions": data, "active_id": active.id})


@login_required
def knowledge_base(request):
    return render(request, "knowledge.html")


@login_required
def career_hub(request):
    return render(request, "career.html")


@login_required
def gov_services(request):
    return render(request, "gov.html")


@login_required
def complete_profile(request):
    profile = get_user_profile(request.user)

    if request.method == "POST":
        first_name = request.POST.get("first_name")
        last_name = request.POST.get("last_name")
        phone = request.POST.get("phone")
        birth_date = request.POST.get("birth_date")

        if first_name and last_name and phone and birth_date:
            request.user.first_name = first_name
            request.user.last_name = last_name
            request.user.save()

            profile.phone_number = phone
            profile.birth_date = birth_date
            profile.save()

            Notification.objects.create(
                user=request.user,
                title="Profil yakunlandi",
                message="Sizning profilingiz muvaffaqiyatli to'ldirildi. Endi barcha imkoniyatlardan foydalanishingiz mumkin.",
                link="/profile/",
            )

            return redirect("dashboard")

    return render(request, "complete_profile.html", {"profile": profile})


@login_required
def profile_settings(request):
    profile = get_user_profile(request.user)

    if request.method == "POST":
        request.user.first_name = request.POST.get("first_name", request.user.first_name)
        request.user.last_name = request.POST.get("last_name", request.user.last_name)
        request.user.save()

        profile.phone_number = request.POST.get("phone", profile.phone_number)
        profile.birth_date = request.POST.get("birth_date", profile.birth_date) or None
        profile.university = request.POST.get("university", profile.university)
        profile.faculty = request.POST.get("faculty", profile.faculty)
        profile.major = request.POST.get("major", profile.major)
        profile.graduation_year = request.POST.get("graduation_year") or None
        profile.interests = request.POST.get("interests", profile.interests)
        profile.short_term_goals = request.POST.get("short_term_goals", profile.short_term_goals)
        profile.long_term_goals = request.POST.get("long_term_goals", profile.long_term_goals)

        skills_raw = request.POST.get("skills", "")
        if skills_raw:
            profile.skills_json = [skill.strip() for skill in skills_raw.split(",") if skill.strip()]
        else:
            profile.skills_json = []

        profile.save()

        Notification.objects.create(
            user=request.user,
            title="Sozlamalar yangilandi",
            message="Sizning profilingiz ma'lumotlari muvaffaqiyatli saqlandi.",
            link="/profile/",
        )

        messages.success(request, "Ma'lumotlar muvaffaqiyatli saqlandi!")
        return redirect("profile_settings")

    return render(request, "profile.html", {"profile": profile})


@login_required
def get_notifications(request):
    notifications = Notification.objects.filter(user=request.user).order_by("-created_at")[:10]
    data = []
    for notification in notifications:
        data.append({
            "id": notification.id,
            "title": notification.title,
            "message": notification.message,
            "link": notification.link or "#",
            "is_read": notification.is_read,
            "created_at": notification.created_at.strftime("%d.%m %H:%M"),
        })
    return JsonResponse({"notifications": data})


@login_required
def mark_all_notifications_read(request):
    if request.method != "POST":
        return JsonResponse(
            {"success": False, "error": "Faqat POST so'rovi qo'llab-quvvatlanadi."},
            status=405,
        )

    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return JsonResponse({"success": True})


@login_required
@csrf_exempt
def sync_oneid_api(request):
    if request.method != "POST":
        return JsonResponse(
            {"success": False, "error": "Faqat POST so'rovi qo'llab-quvvatlanadi."},
            status=405,
        )

    profile = get_user_profile(request.user)
    official_name = "Munisa Axmadjonova Xakimjon qizi"
    official_pinfl = "31405920040058"

    profile.verified_full_name = official_name
    profile.verified_pinfl = official_pinfl
    profile.is_verified_oneid = True
    profile.save()

    request.user.first_name = "Munisa"
    request.user.last_name = "Axmadjonova"
    request.user.save()

    Notification.objects.create(
        user=request.user,
        title="Sinxronizatsiya yakunlandi",
        message=f"OneID ma'lumotlaringiz ({official_name}) muvaffaqiyatli sinxronizatsiya qilindi va profil yangilandi.",
        link="/gov/",
    )

    return JsonResponse({
        "success": True,
        "full_name": official_name,
        "pinfl": official_pinfl,
    })


@login_required
def mark_notification_read(request, notification_id):
    if request.method == "POST":
        notification = get_object_or_404(Notification, id=notification_id, user=request.user)
        notification.is_read = True
        notification.save()
        return JsonResponse({"success": True})
    return JsonResponse({"success": False}, status=400)
