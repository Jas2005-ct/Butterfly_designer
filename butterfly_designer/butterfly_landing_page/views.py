from django.shortcuts import render, redirect
from django.http import HttpResponse
from .models import Items,About,Employee,CustomerReview

# Create your views here.

def home(request):
    about=About.objects.first()
    employee=Employee.objects.all().order_by('-experience')
    reviews=CustomerReview.objects.all().order_by('-created_at')[:8]
    items = Items.objects.all()[:5]
    employee_det = []
    for i in employee:
        employee_det.append({
            'id':i.id,
            'name':i.name,
            'best_known_for':i.best_known_for,
            'image': i.image.url if i.image else None
            
        })

    context = {
        'about': about,
        'employees':employee_det,
        'reviews': reviews,
        'items': items,
    }
    return render(request, 'home.html', context)


def employee_detail(request,id):
    employee=Employee.objects.get(id=id)
    context = {
        'employee': employee,
    }
    return render(request, 'employee_detail.html', context)


def review_form(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        rating = request.POST.get('rating')
        review = request.POST.get('review')
        CustomerReview.objects.create(name=name, rating=rating, review=review)
        return redirect('home')
    return render(request, 'review_form.html')