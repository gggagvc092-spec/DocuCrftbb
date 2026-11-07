import os, io, re, json, html, sqlite3, asyncio
from datetime import datetime, date, timedelta
from collections import Counter
import streamlit as st
import requests

try:
    import google.generativeai as genai
except: pass
try:
    import edge_tts
except: pass
try:
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
except: pass
try:
    from docx import Document as DocxDocument
    from docx.shared import Pt
except: pass

# ==========================================
# 1. الثوابت والإعدادات
# ==========================================
DESIGNS = {
    "royal": {"name": "ملكي (أزرق/ذهبي)", "primary": "#1E3A8A", "accent": "#D97706", "bg": "#F8FAFC", "text": "#1E293B"},
    "emerald": {"name": "زمردي (أخضر/فيروزي)", "primary": "#047857", "accent": "#10B981", "bg": "#F0FDF4", "text": "#064E3B"},
    "midnight": {"name": "ليلي (بنفسجي/رمادي)", "primary": "#312E81", "accent": "#6366F1", "bg": "#111827", "text": "#F3F4F6"},
    "rose": {"name": "وردي (أحمر/وردي)", "primary": "#BE123C", "accent": "#FB7185", "bg": "#FFF1F2", "text": "#4C0519"},
    "minimal": {"name": "بسيط (أسود/أبيض)", "primary": "#374151", "accent": "#9CA3AF", "bg": "#FFFFFF", "text": "#111827"}
}
REVIEW_INTERVALS = [1, 2, 4, 7, 14, 30, 60, 90, 180, 365] # 10 مراحل
TRANSLATION_LANGUAGES = {"ar": "العربية", "en": "الإنجليزية", "fr": "الفرنسية", "tr": "التركية", "es": "الإسبانية"}
HIGHLIGHT_COLORS = {"أصفر": "#FEF08A", "أخضر": "#BBF7D0", "أزرق": "#BFDBFE", "وردي": "#FBCFE8"}

# ==========================================
# 2. قاعدة البيانات
# ==========================================
DB = "docucraft_v4.db"
def db(): 
    c = sqlite3.connect(DB, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE, name TEXT);
    CREATE TABLE IF NOT EXISTS settings(user_id INTEGER PRIMARY KEY, data TEXT);
    CREATE TABLE IF NOT EXISTS docs(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, title TEXT, raw_text TEXT, data TEXT, design TEXT, word_count INTEGER, created_at TEXT);
    CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id INTEGER, next_review TEXT, stage INTEGER, history TEXT);
    """)
    c.commit(); c.close()

def get_settings(uid):
    c = db(); row = c.execute("SELECT data FROM settings WHERE user_id=?", (uid,)).fetchone(); c.close()
    return json.loads(row["data"]) if row else {"theme": "royal", "align": "right", "api_key": ""}

def save_settings(uid, data):
    c = db(); c.execute("INSERT OR REPLACE INTO settings(user_id, data) VALUES(?, ?)", (uid, json.dumps(data))); c.commit(); c.close()

# ==========================================
# 3. الذكاء الاصطناعي، الصوت، والصور
# ==========================================
def ai_init(key):
    if key: 
        try: genai.configure(api_key=key); return True
        except: return False
    return False

def ai_call(prompt, is_json=False, use_pro=False):
    try:
        model_name = "gemini-1.5-pro" if use_pro else "gemini-1.5-flash"
        model = genai.GenerativeModel(model_name)
        if is_json: prompt += "\n\nأعد النتيجة بصيغة JSON فقط."
        return model.generate_content(prompt).text.strip()
    except Exception as e: return str(e)

def parse_json(txt, fallback={}):
    try:
        match = re.search(r'\{.*\}|\[.*\]', txt, re.DOTALL)
        if match: return json.loads(match.group(0))
    except: pass
    return fallback

def gen_tts(text, voice="ar-EG-SalmaNeural"):
    async def _gen():
        comm = edge_tts.Communicate(text[:2000], voice)
        audio = b""
        async for chunk in comm.stream():
            if chunk["type"] == "audio": audio += chunk["data"]
        return audio
    try:
        loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        return loop.run_until_complete(_gen())
    except: return None

def get_cover(topic):
    import urllib.parse
    return f"https://image.pollinations.ai/prompt/{urllib.parse.quote(topic)}?width=800&height=400&nologo=true"

def build_doc_structure(text, title):
    prompt = f"""أنت محرر محترف. حلل النص وأعد بناءه بشكل احترافي جداً.
المطلوب:
1. عنوان رئيسي (title).
2. مقدمة عامة (intro).
3. أسباب دراسة الموضوع في نقاط (reasons).
4. تقسيم النص لفصول، كل فصل له عنوان (heading) ومحتوى (content).

JSON:
{{
  "title": "العنوان",
  "intro": "المقدمة...",
  "reasons": ["سبب 1", "سبب 2"],
  "sections": [{{"heading": "عنوان", "content": "نص"}}]
}}
النص الخام: {text[:8000]}"""
    data = parse_json(ai_call(prompt, is_json=True), {"sections": [{"heading": "المحتوى", "content": text}]})
    if "title" not in data or not data["title"]: data["title"] = title or "مستند جديد"
    data["cover"] = get_cover(data["title"])
    return data

# ==========================================
# 4. التصميم والـ CSS (حل مشكلة الكلام الرأسي)
# ==========================================
def apply_theme(theme_key, align):
    t = DESIGNS.get(theme_key, DESIGNS["royal"])
    align_val = "right" if align == "right" else ("center" if align == "center" else "left")
    st.markdown(f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700&display=swap');
    #MainMenu, footer, header {{visibility: hidden;}}
    
    /* إصلاح اتجاه الصفحة */
    .stApp {{ font-family: 'Cairo', sans-serif !important; background-color: {t['bg']} !important; color: {t['text']} !important; }}
    .block-container {{ direction: rtl; text-align: {align_val}; padding-top: 2rem !important; }}
    
    /* إصلاح المدخلات */
    input, textarea {{ direction: rtl !important; text-align: {align_val} !important; }}
    
    /* البطاقات */
    .glass-card {{
        background: rgba(255, 255, 255, 0.05);
        backdrop-filter: blur(10px);
        border: 1px solid rgba(128, 128, 128, 0.2);
        border-radius: 16px; padding: 24px; margin-bottom: 20px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.05);
    }}
    
    /* العناوين */
    h1, h2, h3 {{ color: {t['primary']} !important; font-weight: 700 !important; }}
    .doc-title {{ font-size: 2.5em; margin-bottom: 10px; text-align: center; }}
    .section-heading {{ color: {t['primary']}; font-size: 1.8em; border-bottom: 3px solid {t['accent']}; padding-bottom: 5px; margin-top: 30px; }}
    .paragraph {{ font-size: 1.15em; line-height: 1.9; margin-bottom: 15px; }}
    
    /* الأزرار */
    .stButton>button {{
        border-radius: 10px !important; font-weight: 600 !important; border: none !important;
        background: {t['primary']}22 !important; color: {t['primary']} !important; transition: 0.2s;
    }}
    .stButton>button:hover {{ background: {t['primary']} !important; color: white !important; }}
    .stButton>button[kind="primary"] {{ background: {t['primary']} !important; color: white !important; }}
    </style>
    """, unsafe_allow_html=True)

# ==========================================
# 5. دوال التصدير والتحليلات
# ==========================================
def export_pdf(doc):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(210*mm, 297*mm))
    c.setFont("Helvetica", 16); c.drawString(50, 800, "DocuCraft PDF Export")
    # Simplified for cloud compatibility
    c.save(); return buf.getvalue()

def export_md(doc):
    d = json.loads(doc["data"])
    txt = f"# {d.get('title','')}\n\n## مقدمة\n{d.get('intro','')}\n\n"
    for sec in d.get("sections", []): txt += f"## {sec['heading']}\n{sec['content']}\n\n"
    return txt.encode('utf-8')

def get_analytics(text):
    words = re.findall(r'\w+', text)
    cnt = Counter(words)
    return len(words), [w for w, c in cnt.most_common(20) if len(w) > 3]

# ==========================================
# 6. الصفحات
# ==========================================
def page_auth():
    st.markdown("<div style='text-align:center; padding:50px;'><h1>📘 DocuCraft Pro V4</h1><p>مساحة عمل ذكية</p></div>", unsafe_allow_html=True)
    tab1, tab2 = st.tabs(["دخول", "حساب جديد"])
    with tab1:
        email = st.text_input("البريد الإلكتروني", key="l_e")
        if st.button("دخول", type="primary", use_container_width=True):
            c = db(); row = c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone(); c.close()
            if row: st.session_state.user = {"id": row["id"]}; st.rerun()
            else: st.error("غير موجود")
    with tab2:
        email2 = st.text_input("بريد جديد", key="s_e")
        if st.button("إنشاء", type="primary", use_container_width=True):
            c = db(); c.execute("INSERT INTO users(email) VALUES(?)", (email2,)); c.commit()
            uid = c.execute("SELECT last_insert_rowid()").fetchone()[0]; c.close()
            st.session_state.user = {"id": uid}; st.rerun()

def page_library(uid, settings):
    st.markdown("<h2>📚 مكتبتي</h2>", unsafe_allow_html=True)
    c = db(); docs = c.execute("SELECT * FROM docs WHERE user_id=? ORDER BY id DESC", (uid,)).fetchall(); c.close()
    if not docs: st.info("لا يوجد مستندات. ابدأ الآن!")
    for d in docs:
        st.markdown(f'<div class="glass-card"><h3>{d["title"]}</h3><p style="color:gray;">📝 {d["word_count"]} كلمة</p></div>', unsafe_allow_html=True)
        col1, col2 = st.columns(2)
        if col1.button("📖 فتح", key=f"o_{d['id']}"): st.session_state.doc_id = d['id']; st.session_state.page = "doc"; st.rerun()
        if col2.button("🗑️ حذف", key=f"d_{d['id']}"): c=db(); c.execute("DELETE FROM docs WHERE id=?", (d['id'],)); c.commit(); c.close(); st.rerun()

def page_new(uid, settings):
    st.markdown("<h2>✨ مستند جديد</h2>", unsafe_allow_html=True)
    text = st.text_area("الصق النص الخام هنا:", height=300)
    design = st.selectbox("التصميم", list(DESIGNS.keys()), format_func=lambda x: DESIGNS[x]["name"])
    if st.button("🚀 تحويل لملف احترافي", type="primary", use_container_width=True):
        if not text: return
        with st.spinner("جاري التحليل..."):
            data = build_doc_structure(text, "مستند")
            wc, _ = get_analytics(text)
            c = db()
            c.execute("INSERT INTO docs (user_id, title, raw_text, data, design, word_count, created_at) VALUES (?,?,?,?,?,?,?)",
                      (uid, data["title"], text, json.dumps(data, ensure_ascii=False), design, wc, datetime.now().isoformat()))
            c.commit(); new_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]; c.close()
            st.session_state.doc_id = new_id; st.session_state.page = "doc"; st.rerun()

def page_doc(uid, settings):
    c = db(); doc = c.execute("SELECT * FROM docs WHERE id=?", (st.session_state.doc_id,)).fetchone(); c.close()
    if not doc: return
    data = json.loads(doc["data"])
    
    if st.button("← رجوع للمكتبة"): st.session_state.page = "lib"; st.rerun()
    
    # Tabs
    t1, t2, t3, t4 = st.tabs(["📖 القراءة", "📊 التحليلات", "🔁 المراجعة", "📤 التصدير"])
    
    with t1:
        if data.get("cover"): st.image(data["cover"], use_column_width=True)
        st.markdown(f"<h1 class='doc-title'>{doc['title']}</h1>", unsafe_allow_html=True)
        
        if data.get("intro"):
            st.markdown(f'<div class="glass-card"><h3>المقدمة</h3><p>{data["intro"]}</p>', unsafe_allow_html=True)
            if data.get("reasons"): st.markdown("<h4>أسباب الدراسة:</h4><ul>" + "".join([f"<li>{r}</li>" for r in data["reasons"]]) + "</ul>", unsafe_allow_html=True)
            st.markdown("</div>", unsafe_allow_html=True)
            
        for i, sec in enumerate(data.get("sections", [])):
            st.markdown(f"<div id='sec_{i}' class='section-heading'>{sec['heading']}</div>", unsafe_allow_html=True)
            st.markdown(f"<div class='paragraph'>{sec['content']}</div>", unsafe_allow_html=True)
            
            # شريط الأدوات العائم (بديل الضغط المطول)
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                with st.popover("🌐 ترجمة"):
                    lang = st.selectbox("اللغة", list(TRANSLATION_LANGUAGES.keys()), key=f"tl_{i}")
                    if st.button("ترجم", key=f"tb_{i}"): st.info(ai_call(f"ترجم: {sec['content']} إلى {TRANSLATION_LANGUAGES[lang]}", use_pro=True))
            with c2:
                with st.popover("🔊 صوت"):
                    if st.button("اقرأ بصوت طبيعي", key=f"ts_{i}"):
                        audio = gen_tts(sec['content'])
                        if audio: st.audio(audio, format="audio/mp3")
            with c3:
                with st.popover("🎧 شرح"):
                    if st.button("شرح مبسط", key=f"ex_{i}"):
                        expl = ai_call(f"اشرح بتبسيط: {sec['content'][:1000]}")
                        st.write(expl)
                        audio = gen_tts(expl)
                        if audio: st.audio(audio, format="audio/mp3")
            with c4:
                with st.popover("✏️ تعديل"):
                    new_t = st.text_area("تعديل", value=sec['content'], key=f"ed_{i}")
                    if st.button("حفظ", key=f"sv_{i}"):
                        data["sections"][i]["content"] = new_t
                        c=db(); c.execute("UPDATE docs SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), doc["id"])); c.commit(); c.close(); st.rerun()
            st.divider()

    with t2:
        wc, keywords = get_analytics(doc["raw_text"])
        st.metric("إجمالي الكلمات", wc)
        st.markdown("### الكلمات المفتاحية الأكثر تكراراً")
        st.write(", ".join(keywords))
        
    with t3:
        st.markdown("### 🔁 نظام المراجعة المتباعدة (10 مراحل)")
        c = db(); rev = c.execute("SELECT * FROM reviews WHERE doc_id=?", (doc["id"],)).fetchone(); c.close()
        if not rev:
            if st.button("أضف لخطة المراجعة"):
                c=db(); c.execute("INSERT INTO reviews (doc_id, next_review, stage, history) VALUES (?,?,?,?)", (doc["id"], date.today().isoformat(), 0, "[]")); c.commit(); c.close(); st.rerun()
        else:
            st.info(f"المرحلة الحالية: {rev['stage']+1} | المراجعة القادمة: {rev['next_review']}")
            if st.button("✅ راجع الآن"):
                stage = min(9, rev["stage"] + 1)
                next_d = (date.today() + timedelta(days=REVIEW_INTERVALS[stage])).isoformat()
                c=db(); c.execute("UPDATE reviews SET stage=?, next_review=? WHERE id=?", (stage, next_d, rev["id"])); c.commit(); c.close(); st.success("تم التحديث!"); time.sleep(1); st.rerun()

    with t4:
        st.download_button("📄 تحميل HTML", json.dumps(data, ensure_ascii=False).encode('utf-8'), f"{doc['title']}.json")
        st.download_button("📝 تحميل Markdown", export_md(doc), f"{doc['title']}.md")

# ==========================================
# 7. التشغيل
# ==========================================
def main():
    st.set_page_config(page_title="DocuCraft V4", layout="wide")
    init_db()
    if "user" not in st.session_state: st.session_state.user = None
    if "page" not in st.session_state: st.session_state.page = "lib"
    
    if not st.session_state.user:
        apply_theme("royal", "center")
        page_auth()
        return
        
    uid = st.session_state.user["id"]
    settings = get_settings(uid)
    ai_init(settings.get("api_key", ""))
    apply_theme(settings.get("theme", "royal"), settings.get("align", "right"))
    
    with st.sidebar:
        st.markdown("<h2>📘 DocuCraft</h2>", unsafe_allow_html=True)
        if st.button("📚 المكتبة", use_container_width=True): st.session_state.page = "lib"; st.rerun()
        if st.button("➕ مستند جديد", use_container_width=True): st.session_state.page = "new"; st.rerun()
        if st.button("⚙️ الإعدادات", use_container_width=True): st.session_state.page = "set"; st.rerun()
        if st.button("🚪 خروج", use_container_width=True): st.session_state.user = None; st.rerun()

    if st.session_state.page == "lib": page_library(uid, settings)
    elif st.session_state.page == "new": page_new(uid, settings)
    elif st.session_state.page == "doc": page_doc(uid, settings)
    elif st.session_state.page == "set":
        st.markdown("<h2>⚙️ الإعدادات</h2>", unsafe_allow_html=True)
        key = st.text_input("Gemini API Key", value=settings.get("api_key", ""), type="password")
        theme = st.selectbox("الثيم", list(DESIGNS.keys()), format_func=lambda x: DESIGNS[x]["name"])
        align = st.selectbox("محاذاة النص", ["right", "center", "left"], format_func=lambda x: {"right":"يمين","center":"وسط","left":"شمال"}[x])
        if st.button("💾 حفظ", type="primary"):
            save_settings(uid, {"api_key": key, "theme": theme, "align": align}); st.success("تم الحفظ"); st.rerun()

if __name__ == "__main__": main()