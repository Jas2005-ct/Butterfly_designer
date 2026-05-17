import qrcode
import os

# Update this with your actual server IP or Domain (e.g., http://192.168.1.10:8000/review/)
url = "http://127.0.0.1:8000/review/"

qr = qrcode.QRCode(
    version=1,
    error_correction=qrcode.constants.ERROR_CORRECT_H, # Higher error correction for branding
    box_size=10,
    border=4,
)
qr.add_data(url)
qr.make(fit=True)

img = qr.make_image(fill_color="#1e1b4b", back_color="white") # Navy blue fill for premium look

# Ensure static/images directory exists
os.makedirs("static/images", exist_ok=True)
img.save("static/images/qr_review.png")
print("QR Code generated at: static/images/qr_review.png")