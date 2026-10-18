# Render Server — SIM Bridge v0.8

هذا الخادم مخصّص لتطبيق Redmi Gateway v0.8 المتوافق وواجهة iPhone في `index.html`، وليس لخادم TCP القديم في Google Cloud.

## الرفع والنشر
1. ارفع الملفات هنا **بمحتوياتها إلى جذر** مستودع `simbridge-relay` الخاص على GitHub.
2. في Render: New Web Service → المستودع → Python → Free.
3. Build Command: `pip install -r requirements.txt`.
4. Start Command: `python relay.py`.
5. Environment: `BRIDGE_GATEWAY_KEY` و`BRIDGE_IPHONE_KEY` (قيمتان عشوائيتان مختلفتان لا تقل كل منهما عن 32 حرفًا). **لا تضعهما في ملفات GitHub**.
6. افتح `https://YOUR-SERVICE.onrender.com/healthz` للتأكد من الحالة، ثم افتح `/` لصفحة iPhone.

## الاتصال
- Android v0.8 يتصل خارجيًا بـ`wss://YOUR-SERVICE.onrender.com/ws/gateway` باستخدام BRIDGE_GATEWAY_KEY.
- iPhone Safari يتصل بـ`wss://YOUR-SERVICE.onrender.com/ws/iphone` باستخدام BRIDGE_IPHONE_KEY.
- يدخل المستخدم رمز الربط من Redmi أيضًا؛ يستخدمه الجهازان لاشتقاق مفاتيح AES-256-GCM منفصلة لكل اتجاه، مع HKDF-SHA256 وملح جديد في كل جلسة.
- Render يمرّر الحزم الثنائية المشفّرة، والصفحة لا تخزن مفتاح Render في التخزين المحلي.

## الخصوصية
Render ينهي TLS الخارجي؛ بيانات الصوت والتحكم محمية بتشفير إضافي في التطبيقين، لكن HTML/JavaScript يأتي من Render نفسه، ولذلك **مشغّل استضافة يستطيع تعديل الصفحة نظريًا وسرقة مفاتيح المستخدم**. لا يُعتبر هذا تصميم تشفير ضد استضافة خبيثة. للحصول على ذلك، استخدم تطبيق iPhone أصليًا وموثوق المصدر. لا ترفع ملفات `.p12` ولا مفاتيح TLS الخاصة ولا أسرار Telegram.

**الاختبارات البرمجية لا تعني أن المكالمات نجحت على الأجهزة.** اقرأ README_AR.md في الحزمة الكاملة للتعليمات والتحذيرات.
