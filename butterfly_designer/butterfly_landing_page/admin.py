from django.contrib import admin
from .models import Items,About,Employee,CustomerReview

@admin.register(Items)
class ItemsAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'image')
    list_filter = ('name', 'price')
    search_fields = ('name', 'price')
    ordering = ('name', 'price')


admin.site.register(About)
admin.site.register(Employee)
admin.site.register(CustomerReview)
# Register your models here.
