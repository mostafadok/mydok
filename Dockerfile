# استخدام بيئة بايثون رسمية وخفيفة
FROM python:3.11-slim

# إعداد مجلد العمل داخل السيرفر
WORKDIR /app

# نسخ ملف المكاتب وتثبيتها
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# تثبيت متصفح Playwright المخفي واعتمادات اللينكس اللازمة لتشغيله
RUN playwright install chromium
RUN playwright install-deps

# نسخ باقي الملفات (السكربت)
COPY . .

# تشغيل التطبيق (Render سيقوم بتوفير الـ PORT تلقائياً)
CMD ["python", "api.py"]
