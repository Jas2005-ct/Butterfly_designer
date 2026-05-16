from django.shortcuts import render
from django.http import HttpResponse
from .models import Items,About,Employee,CustomerReview

# Create your views here.

def home(request):
    about=About.objects.first()
    employee=Employee.objects.all()
    reviews=CustomerReview.objects.all()
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