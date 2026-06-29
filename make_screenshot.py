from PIL import Image, ImageDraw

img_path = r"C:\Users\ssfnn\.gemini\antigravity\brain\b96a8487-1baf-4979-af82-9c75aa8b0150\media__1782752772036.png"
out_path = r"assets\screenshot.jpg"

try:
    img = Image.open(img_path).convert("RGB")
    width, height = img.size
    
    draw = ImageDraw.Draw(img)
    bg_color = img.getpixel((width - 150, height - 20))
    
    x0 = width - 120
    y0 = height - 50
    x1 = width
    y1 = height
    
    draw.rectangle([x0, y0, x1, y1], fill=bg_color)
    img.save(out_path, "JPEG", quality=95)
    print(f"Saved screenshot to {out_path}")
except Exception as e:
    print("Error:", e)
