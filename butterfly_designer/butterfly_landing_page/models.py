from django.db import models

# Create your models here.

class Items(models.Model):
    name=models.CharField(max_length=100)
    price=models.DecimalField(max_digits=10, decimal_places=2)
    image=models.ImageField(upload_to='images/')
    
    def __str__(self):
        return self.name

class About(models.Model):
    name = models.CharField(max_length=100)
    address=models.TextField()
    phone=models.CharField(max_length=15)
    email=models.EmailField()
    

    class Meta:
        verbose_name_plural="About"
    
    def __str__(self):
        return self.name

    def save(self,*args,**kwargs):
        if About.objects.exists():
            raise ValueError("Only one About object is allowed")
        super().save(*args, **kwargs)
    