from PIL import Image, ImageDraw

img_path = r"C:\Users\ssfnn\.gemini\antigravity\brain\b96a8487-1baf-4979-af82-9c75aa8b0150\media__1782752772036.png"
out_path = r"assets\hero.jpg"

try:
    img = Image.open(img_path).convert("RGB")
    width, height = img.size
    
    # 時間などのタスクバー右下部分を黒塗りする
    # タスクバーはおおよそ下から 50px 程度、時間は右から 150px 程度に位置すると推定
    draw = ImageDraw.Draw(img)
    # 右下を黒塗り (x0, y0, x1, y1)
    # タスクバーの背景色っぽい色で塗るか、黒で塗る
    # 色をサンプリングする
    bg_color = img.getpixel((width - 150, height - 20))
    
    # 隠す領域: 右から 120px、下から 50px
    x0 = width - 120
    y0 = height - 50
    x1 = width
    y1 = height
    
    draw.rectangle([x0, y0, x1, y1], fill=bg_color)
    
    # 保存
    img.save(out_path, "JPEG", quality=95)
    print(f"Saved to {out_path}, Size: {width}x{height}")
except Exception as e:
    print("Error:", e)
