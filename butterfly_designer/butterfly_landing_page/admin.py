from django.contrib import admin
from .models import Items,About

@admin.register(Items)
class ItemsAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'image')
    list_filter = ('name', 'price')
    search_fields = ('name', 'price')
    ordering = ('name', 'price')


admin.register(About)
# Register your models here.
