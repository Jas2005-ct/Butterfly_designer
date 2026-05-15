from django.shortcuts import render
from django.http import HttpResponse

# Create your views here.

def home(request):
    context = {
        'hero_img': '/static/images/hero.png',
        'item1_img': '/static/images/gown.png',
        'item2_img': '/static/images/suit.png',
    }
    return render(request, 'home.html', context)