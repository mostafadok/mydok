# استخدام النسخة الرسمية من مايكروسوفت التي تحتوي على كل ملفات النظام لتشغيل المتصفحات
FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

# تعيين مجلد العمل داخل السيرفر
WORKDIR /app

# نسخ ملف المكتبات
COPY requirements.txt .

# تثبيت المكتبات
RUN pip install --no-cache-dir -r requirements.txt

# نسخ كود البايثون
COPY api.py .

# تحديد البورت
EXPOSE 8000

# أمر تشغيل السيرفر عند بدء ريندر
CMD ["python", "api.py"]
