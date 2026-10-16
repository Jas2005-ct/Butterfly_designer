from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404, redirect, render

from .decorators import admin_required
from .models import Role


def shop_login(request):
    if request.user.is_authenticated:
        return redirect("orders:board")
    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            return redirect("orders:board")
    else:
        form = AuthenticationForm()
    return render(request, "shop/login.html", {"form": form})


@login_required
def shop_logout(request):
    if request.method != "POST":
        return redirect("orders:board")
    logout(request)
    return redirect("accounts:login")


@admin_required
def user_list(request):
    users = User.objects.select_related("profile").order_by("username")
    return render(request, "shop/accounts/user_list.html", {"users": users})


@admin_required
def user_create(request):
    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""
        role = request.POST.get("role") or Role.MANAGER
        if not username or not password:
            messages.error(request, "Username and password are required.")
        elif len(password) < 6:
            messages.error(request, "Password must be at least 6 characters.")
        elif role not in Role.values:
            messages.error(request, "Invalid role.")
        elif User.objects.filter(username=username).exists():
            messages.error(request, "Username already exists.")
        else:
            user = User.objects.create_user(username=username, password=password)
            user.profile.role = role
            user.profile.save(update_fields=["role"])
            messages.success(request, f"User '{username}' created.")
            return redirect("accounts:user_list")
    return render(request, "shop/accounts/user_create.html", {"roles": Role.choices})


@admin_required
def user_toggle_active(request, user_id):
    if request.method != "POST":
        return redirect("accounts:user_list")
    target = get_object_or_404(User.objects.select_related("profile"), id=user_id)
    if target == request.user:
        messages.error(request, "You cannot deactivate your own account.")
    else:
        target.is_active = not target.is_active
        target.save(update_fields=["is_active"])
        state = "activated" if target.is_active else "deactivated"
        messages.success(request, f"User '{target.username}' {state}.")
    return redirect("accounts:user_list")
