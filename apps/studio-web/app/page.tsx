"use client";

import { useState } from "react";

const stages = [
  ["01", "فهم الفكرة", "استلام وصف الفيديو وتحديد الهدف والأسلوب"],
  ["02", "تحليل المواد", "فحص الصور والفيديو والصوت والمراجع"],
  ["03", "خطة المشاهد", "Storyboard وتوقيتات وحركة الكاميرا"],
  ["04", "الإنتاج", "التوليد والموشن والصوت والمونتاج"],
  ["05", "مراجعة الجودة", "فحص فني وبصري ومطابقة المنتج"],
];

export default function HomePage() {
  const [brief, setBrief] = useState("");
  const [notice, setNotice] = useState("واجهة أولية؛ رفع الملفات وربط API غير موصولين بعد.");

  function submitBrief() {
    if (!brief.trim()) {
      setNotice("اكتب فكرة الفيديو أولاً.");
      return;
    }
    setNotice("تم تجهيز النص محلياً في الواجهة التجريبية. لم تُرسل مهمة إلى السيرفر.");
  }

  return (
    <main className="studio-shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark">V</span><div><b>AI VIDEO STUDIO</b><small>الإصدار 2.0 · مساحة خاصة</small></div></div>
        <nav>
          <a className="active" href="#director">✦ المدير التنفيذي</a>
          <a href="#projects">▦ المشاريع</a>
          <a href="#agents">◈ الوكلاء</a>
          <a href="#monitor">⌁ مراقبة المعالجة</a>
          <a href="#settings">⚙ الإعدادات</a>
        </nav>
        <div className="sidebar-foot"><span className="dot" /> وضع تجريبي · لا يوجد GPU متصل</div>
      </aside>

      <section className="main-area">
        <header className="topbar">
          <div><span className="eyebrow">مساحة الإنتاج الخاصة بك</span><h1>استوديو الفيديو الذكي</h1></div>
          <div className="user-pill"><span className="dot muted" /> مستخدم واحد</div>
        </header>

        <section className="hero" id="director">
          <div className="hero-copy">
            <span className="kicker">AI EXECUTIVE DIRECTOR</span>
            <h2>احكِ له الفكرة.<br/><em>ودعه يخطط للإنتاج.</em></h2>
            <p>صف الإعلان الذي تريده، أرفق صور المنتج والمراجع، وسيحوّل النظام طلبك إلى خطة إنتاج قابلة للمراجعة والتنفيذ.</p>
          </div>
          <div className="hero-orb"><span>AI</span><i /><b /></div>
        </section>

        <section className="workspace-card">
          <div className="section-heading">
            <div><span className="eyebrow">ابدأ مشروعاً جديداً</span><h3>ما الفيديو الذي تريد إنتاجه؟</h3></div>
            <span className="chip">يدعم العربية</span>
          </div>
          <textarea value={brief} onChange={(e) => setBrief(e.target.value)} placeholder="مثال: أريد إعلاناً سينمائياً مدته 60 ثانية لساعة فاخرة. أريد مشاهد بشرية ولقطات قريبة للمنتج وموشن جرافيك..." />
          <div className="upload-row">
            <button className="upload-button" type="button" onClick={() => setNotice("رفع الملفات غير موصول بعد؛ سيضاف مع Media Ingestion API.")}>＋ إضافة صور وفيديوهات</button>
            <span>صور المنتج · فيديوهات المصدر · شعار · صوت مرجعي</span>
            <button className="primary-button" onClick={submitBrief}>إنشاء خطة الإنتاج <b>←</b></button>
          </div>
          <p className="notice" role="status">{notice}</p>
        </section>

        <section className="lower-grid" id="projects">
          <div className="panel">
            <div className="section-heading"><div><span className="eyebrow">WORKFLOW</span><h3>مراحل الإنتاج</h3></div><span className="quiet">5 مراحل</span></div>
            <div className="stage-list">{stages.map(([number, title, detail]) => <div className="stage" key={number}><span>{number}</span><div><b>{title}</b><small>{detail}</small></div><i>○</i></div>)}</div>
          </div>
          <div className="panel" id="monitor">
            <div className="section-heading"><div><span className="eyebrow">SYSTEM MONITOR</span><h3>حالة النظام</h3></div><span className="status-wait">بانتظار النشر</span></div>
            <div className="metric"><span>GPU Worker</span><b>غير متصل</b></div>
            <div className="metric"><span>نموذج المدير التنفيذي</span><b>غير مثبت</b></div>
            <div className="metric"><span>طابور المهام</span><b>0 مهمة</b></div>
            <div className="metric"><span>الميزانية</span><b>تُضبط لاحقاً</b></div>
            <div className="hint">لن يبدأ أي توليد مكلف قبل موافقتك.</div>
          </div>
        </section>
        <footer>AI Video Studio 2.0 · واجهة هيكلية أولية وليست نسخة إنتاج متصلة بعد.</footer>
      </section>
    </main>
  );
}
