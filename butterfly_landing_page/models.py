from django.db import models

# Create your models here.

class Items(models.Model):
    name=models.CharField(max_length=100)
    price=models.DecimalField(max_digits=10,null=True,blank=True, decimal_places=2)
    image=models.ImageField(upload_to='images/')
    created_at=models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return self.name

class About(models.Model):
    name = models.CharField(max_length=100)
    address=models.TextField()
    phone=models.CharField(max_length=15)
    email=models.EmailField()
    instagram=models.URLField(null=True,blank=True)
    facebook=models.URLField(null=True,blank=True)
    twitter=models.URLField(null=True,blank=True)
    
    

    class Meta:
        verbose_name_plural="About"
    
    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.pk and About.objects.exists():
            raise ValueError("Only one About object is allowed")
        super().save(*args, **kwargs)
       
    

class Employee(models.Model):
    name=models.CharField(max_length=100)
    image=models.ImageField(upload_to='images/',blank=True,null=True)
    experience=models.IntegerField()
    best_known_for=models.CharField(max_length=100)
    about=models.TextField(null=True,blank=True)
    address=models.TextField(null=True,blank=True)
    phone=models.CharField(max_length=15,null=True,blank=True)
    email=models.EmailField(null=True,blank=True)
    social_links=models.URLField(null=True,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class CustomerReview(models.Model):
    name=models.CharField(max_length=100)
    review=models.TextField()
    rating=models.IntegerField()
    created_at=models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return self.name