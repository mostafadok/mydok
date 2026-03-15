# استخدام بيئة مايكروسوفت الرسمية اللي فيها كل ملفات تشغيل المتصفحات
FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

# تحديد مسار العمل
WORKDIR /app

# نسخ ملفات المشروع بتاعك للسيرفر
COPY . /app

# تسطيب مكاتب بايثون
RUN pip install --no-cache-dir -r requirements.txt

# تسطيب متصفح كروم الوهمي
RUN playwright install chromium

# تشغيل السيرفر 
# (لو اسم ملف الكود بتاعك main.py خليها main:app بدل api:app)
CMD uvicorn api:app --host 0.0.0.0 --port ${PORT:-10000}
