"""Seed payloads mirrored from apps/web static sources (content.ts, profile.*.ts, Master CV).

Source approval: owner master career profile, 2026-08-15. Do not invent prose here.
"""

from __future__ import annotations

from typing import TypedDict

# Shared publication slugs (independent fa/en titles).
PUBLICATION_SLUGS = (
    "visual-discourse-presidential-elections",
    "dashboard-design-data-storytelling",
    "vtd-edge-persian-nlp-to-sql",
)

RESEARCH_TOPIC_SLUGS = (
    "pars-sql-vtd-edge",
    "story-driven-dashboard-design",
    "visual-political-communication",
)


class LandingSeed(TypedDict):
    slug: str
    title: str
    body: str
    seo_title: str
    seo_description: str


class ProfileSeed(TypedDict):
    slug: str
    title: str
    body: str
    seo_title: str
    seo_description: str


class ResearchTopicSeed(TypedDict):
    slug: str
    title: str
    summary: str
    motivation: str
    problems: str
    research_questions: str
    methods: str
    future_directions: str


class ResearchStatementSeed(TypedDict):
    slug: str
    title: str
    body: str


class PublicationSeed(TypedDict):
    slug: str
    title: str
    authors: str
    venue: str


class ProjectSeed(TypedDict):
    slug: str
    title: str
    project_type: str
    objective: str
    methods_summary: str
    role: str
    license: str
    code_availability: str
    data_availability: str
    demo_availability: str
    code_url: str
    topic_slugs: tuple[str, ...]
    publication_slugs: tuple[str, ...]


class ArticleSeed(TypedDict):
    slug: str
    title: str
    excerpt: str
    body: str
    license: str


LANDINGS: dict[str, LandingSeed] = {
    "en": {
        "slug": "home",
        "title": "Taha Mohammadi",
        "body": (
            "## Research · Engineering · Design\n\n"
            "Design · Interaction · Engineering · Data · AI\n\n"
            "I build and study human-centered intelligent systems — from interaction "
            "and engineering to data and AI.\n\n"
            "### Explore by perspective\n\n"
            "One identity, multiple entry paths. Choose the route that matches why "
            "you are here.\n\n"
            "**Research** — Professors · Collaborators · Admission\n\n"
            "Research direction, methods and evidence for academic evaluation and "
            "collaboration.\n\n"
            "**Engineering & AI** — Managers · R&D leads · Recruiters\n\n"
            "Systems, decisions, trade-offs and outcomes for technical evaluation.\n\n"
            "**Writing & Learning** — Students · Learners · Readers\n\n"
            "Articles, series and learning resources, documented with reasoning.\n\n"
            "These paths open in a later release. For now, this landing introduces "
            "the identity and direction."
        ),
        "seo_title": "Taha Mohammadi — Human-Centered Intelligent Systems",
        "seo_description": (
            "Interdisciplinary researcher and engineer building human-centered "
            "intelligent systems across design, engineering, data and AI."
        ),
    },
    "fa": {
        "slug": "home",
        "title": "طه محمدی",
        "body": (
            "## پژوهش · مهندسی · طراحی\n\n"
            "طراحی · تعامل · مهندسی · داده · هوش مصنوعی\n\n"
            "سیستم‌های هوشمند انسان‌محور را می‌سازم و مطالعه می‌کنم — از تعامل و "
            "مهندسی تا داده و هوش مصنوعی.\n\n"
            "### مشاهده از نگاه شما\n\n"
            "یک هویت، چند مسیر ورود. مسیری را انتخاب کنید که با دلیل آمدن شما "
            "هم‌خوان است.\n\n"
            "**پژوهش** — استادان · همکاران · پذیرش\n\n"
            "مسیر پژوهش، روش‌ها و شواهد برای ارزیابی و همکاری دانشگاهی.\n\n"
            "**مهندسی و هوش مصنوعی** — مدیران · رهبران تحقیق و توسعه · استخدام‌کنندگان\n\n"
            "سیستم‌ها، تصمیم‌ها، بده‌بستان‌ها و نتایج برای ارزیابی فنی.\n\n"
            "**نوشتن و یادگیری** — دانشجویان · یادگیرندگان · خوانندگان\n\n"
            "مقاله‌ها، مجموعه‌ها و منابع یادگیری، مستندشده با استدلال.\n\n"
            "این مسیرها در نسخهٔ بعدی باز می‌شوند. فعلاً این صفحهٔ فرود هویت و "
            "مسیر را معرفی می‌کند."
        ),
        "seo_title": "طه محمدی — سیستم‌های هوشمند انسان‌محور",
        "seo_description": (
            "پژوهشگر و مهندس میان‌رشته‌ای در ساخت سیستم‌های هوشمند انسان‌محور "
            "در مرز طراحی، مهندسی، داده و هوش مصنوعی."
        ),
    },
}

PROFILES: dict[str, ProfileSeed] = {
    "en": {
        "slug": "about",
        "title": "About",
        "body": (
            "Software engineer and applied AI researcher focused on local LLM systems, "
            "Persian NLP-to-SQL, backend platforms, and human-centered analytical "
            "interfaces. I combine engineering, research, and design to build "
            "privacy-aware systems that translate complex data into understandable "
            "and actionable experiences.\n\n"
            "Taha Mohammadi is a software engineer, applied AI researcher, and "
            "human-centered data-product designer. His work connects enterprise "
            "backend engineering with local language models, natural-language "
            "interfaces, data storytelling, and decision-support systems. He has "
            "contributed to software and analytics initiatives in telecommunications, "
            "electronic payments, education, and public administration, while "
            "developing a research agenda around reliable Persian NLP-to-SQL, edge "
            "AI, explainable analytical interfaces, and privacy-preserving healthcare "
            "systems. His multidisciplinary background in visual communication and "
            "interior architecture informs a distinctive approach that treats software "
            "not only as infrastructure, but also as an interface between complex "
            "data, human cognition, and accountable action.\n\n"
            "Open to PhD opportunities, research collaboration, and professional "
            "opportunities."
        ),
        "seo_title": "About — Taha Mohammadi",
        "seo_description": (
            "Software engineer and applied AI researcher focused on local LLM systems, "
            "Persian NLP-to-SQL, backend platforms, and human-centered analytical "
            "interfaces."
        ),
    },
    "fa": {
        "slug": "about",
        "title": "درباره",
        "body": (
            "مهندس نرم‌افزار و پژوهشگر هوش مصنوعی کاربردی هستم که بر سیستم‌های LLM "
            "محلی، NLP-to-SQL فارسی، سکوهای بک‌اند و رابط‌های تحلیلی انسان‌محور تمرکز "
            "دارم. مهندسی، پژوهش و طراحی را برای ساخت سیستم‌های آگاه از حریم خصوصی که "
            "داده‌های پیچیده را به تجربه‌هایی قابل‌فهم و قابل‌اقدام تبدیل می‌کنند، "
            "در کنار هم قرار می‌دهم.\n\n"
            "Taha Mohammadi مهندس نرم‌افزار، پژوهشگر هوش مصنوعی کاربردی و طراح "
            "محصولات دادهٔ انسان‌محور است. کار او مهندسی بک‌اند سازمانی را به مدل‌های "
            "زبانی محلی، رابط‌های زبان طبیعی، روایت‌گری داده و سیستم‌های پشتیبان تصمیم "
            "پیوند می‌دهد. او در طرح‌های نرم‌افزاری و تحلیلی در حوزه‌های مخابرات، پرداخت "
            "الکترونیکی، آموزش و مدیریت عمومی مشارکت داشته و هم‌زمان برنامهٔ پژوهشی خود "
            "را پیرامون NLP-to-SQL قابل‌اعتماد فارسی، Edge AI، رابط‌های تحلیلی "
            "توضیح‌پذیر و سیستم‌های سلامتِ حافظ حریم خصوصی توسعه می‌دهد. پیشینهٔ "
            "میان‌رشته‌ای او در ارتباط تصویری و معماری داخلی، رویکردی متمایز به او "
            "داده است که نرم‌افزار را نه فقط زیرساخت، بلکه رابطی میان داده‌های پیچیده، "
            "شناخت انسان و اقدام پاسخ‌گو می‌داند.\n\n"
            "برای فرصت‌های دکتری، همکاری پژوهشی و فرصت‌های حرفه‌ای آمادهٔ گفتگو هستم."
        ),
        "seo_title": "درباره — طه محمدی",
        "seo_description": (
            "مهندس نرم‌افزار و پژوهشگر هوش مصنوعی کاربردی در سیستم‌های LLM محلی، "
            "NLP-to-SQL فارسی و رابط‌های تحلیلی انسان‌محور."
        ),
    },
}

RESEARCH_STATEMENTS: dict[str, ResearchStatementSeed] = {
    "en": {
        "slug": "statement",
        "title": "Research statement",
        "body": (
            "<p>Software engineer and applied AI researcher focused on local LLM systems, "
            "Persian NLP-to-SQL, backend platforms, and human-centered analytical "
            "interfaces. I combine engineering, research, and design to build "
            "privacy-aware systems that translate complex data into understandable "
            "and actionable experiences.</p>"
            "<p>Taha Mohammadi is a software engineer, applied AI researcher, and "
            "human-centered data-product designer. His work connects enterprise "
            "backend engineering with local language models, natural-language "
            "interfaces, data storytelling, and decision-support systems. He has "
            "contributed to software and analytics initiatives in telecommunications, "
            "electronic payments, education, and public administration, while "
            "developing a research agenda around reliable Persian NLP-to-SQL, edge "
            "AI, explainable analytical interfaces, and privacy-preserving healthcare "
            "systems.</p>"
            "<h2>Current research directions</h2>"
            "<ul>"
            "<li><strong>PARS-SQL / VTD-Edge</strong> — Local, privacy-first Persian "
            "Text-to-SQL for mental-health and lifestyle analytics with robustness, "
            "safety, and edge deployment constraints.</li>"
            "<li><strong>Story-Driven Dashboard Design Framework</strong> — "
            "Design-science framework for narrative decision-support systems applied "
            "in national public-administration dashboard programmes.</li>"
            "<li><strong>Visual Political Communication Research</strong> — "
            "Comparative study of Reformist and Principlist visual discourse in "
            "Iranian presidential campaigns (1997–2017).</li>"
            "</ul>"
        ),
    },
    "fa": {
        "slug": "statement",
        "title": "بیانیهٔ پژوهشی",
        "body": (
            "<p>مهندس نرم‌افزار و پژوهشگر هوش مصنوعی کاربردی هستم که بر سیستم‌های LLM "
            "محلی، NLP-to-SQL فارسی، سکوهای بک‌اند و رابط‌های تحلیلی انسان‌محور تمرکز "
            "دارم. مهندسی، پژوهش و طراحی را برای ساخت سیستم‌های آگاه از حریم خصوصی که "
            "داده‌های پیچیده را به تجربه‌هایی قابل‌فهم و قابل‌اقدام تبدیل می‌کنند، "
            "در کنار هم قرار می‌دهم.</p>"
            "<p>Taha Mohammadi مهندس نرم‌افزار، پژوهشگر هوش مصنوعی کاربردی و طراح "
            "محصولات دادهٔ انسان‌محور است. کار او مهندسی بک‌اند سازمانی را به مدل‌های "
            "زبانی محلی، رابط‌های زبان طبیعی، روایت‌گری داده و سیستم‌های پشتیبان تصمیم "
            "پیوند می‌دهد. او در طرح‌های نرم‌افزاری و تحلیلی در حوزه‌های مخابرات، پرداخت "
            "الکترونیکی، آموزش و مدیریت عمومی مشارکت داشته و هم‌زمان برنامهٔ پژوهشی خود "
            "را پیرامون NLP-to-SQL قابل‌اعتماد فارسی، Edge AI، رابط‌های تحلیلی "
            "توضیح‌پذیر و سیستم‌های سلامتِ حافظ حریم خصوصی توسعه می‌دهد.</p>"
            "<h2>مسیرهای پژوهشی فعلی</h2>"
            "<ul>"
            "<li><strong>PARS-SQL / VTD-Edge</strong> — Text-to-SQL فارسی محلی و "
            "حریم‌خصوصی‌محور برای تحلیل سلامت روان و سبک زندگی با تمرکز بر استحکام، "
            "ایمنی و محدودیت‌های استقرار edge.</li>"
            "<li><strong>چارچوب طراحی داشبورد روایت‌محور</strong> — چارچوب علم طراحی "
            "برای سامانه‌های روایت‌محور پشتیبان تصمیم در برنامه‌های داشبورد ملی.</li>"
            "<li><strong>پژوهش ارتباطات سیاسی بصری</strong> — مطالعهٔ تطبیقی گفتمان "
            "بصری اصلاح‌طلبان و اصول‌گرایان در کارزارهای انتخابات ریاست‌جمهوری "
            "ایران (1997–2017).</li>"
            "</ul>"
        ),
    },
}

RESEARCH_TOPICS: dict[str, tuple[ResearchTopicSeed, ...]] = {
    "en": (
        {
            "slug": "pars-sql-vtd-edge",
            "title": "PARS-SQL / VTD-Edge",
            "summary": (
                "Local, privacy-first Persian Text-to-SQL system for mental-health and "
                "lifestyle analytics. Supports colloquial Persian, typos, Finglish, "
                "mixed Persian-English, Jalali date expressions, ambiguity handling, "
                "unsafe-query routing, validation, abstention, and explainable output. "
                "Research runtime and edge runtime share a common normalization, linking, "
                "validation, execution, and formatting core. Evaluation plan prioritizes "
                "reliability, safety, robustness, latency, memory, dataset documentation, "
                "human agreement, and reproducibility."
            ),
            "motivation": (
                "Local, privacy-first Persian Text-to-SQL system for mental-health and "
                "lifestyle analytics."
            ),
            "problems": (
                "Supports colloquial Persian, typos, Finglish, mixed Persian-English, "
                "Jalali date expressions, ambiguity handling, unsafe-query routing, "
                "validation, abstention, and explainable output."
            ),
            "research_questions": "",
            "methods": (
                "Research runtime and edge runtime share a common normalization, linking, "
                "validation, execution, and formatting core."
            ),
            "future_directions": (
                "Evaluation plan prioritizes reliability, safety, robustness, latency, "
                "memory, dataset documentation, human agreement, and reproducibility."
            ),
        },
        {
            "slug": "story-driven-dashboard-design",
            "title": "Story-Driven Dashboard Design Framework",
            "summary": (
                "Design-science framework for transforming static KPI panels into "
                "narrative decision-support systems. Integrates data storytelling, DIKW, "
                "SMART/GQM/GQMD, data architecture, visual perception, cognitive load, HCI, "
                "interaction, evaluation, training, governance, and AI-enhanced analytics. "
                "Applied in a national public-administration case with nine organizational "
                "dashboard suites."
            ),
            "motivation": (
                "Design-science framework for transforming static KPI panels into "
                "narrative decision-support systems."
            ),
            "problems": "",
            "research_questions": "",
            "methods": (
                "Integrates data storytelling, DIKW, SMART/GQM/GQMD, data architecture, "
                "visual perception, cognitive load, HCI, interaction, evaluation, training, "
                "governance, and AI-enhanced analytics."
            ),
            "future_directions": (
                "Applied in a national public-administration case with nine organizational "
                "dashboard suites."
            ),
        },
        {
            "slug": "visual-political-communication",
            "title": "Visual Political Communication Research",
            "summary": (
                "Comparative study of Reformist and Principlist visual discourse in "
                "Iranian presidential campaigns from 1997 to 2017. Corpus of 80 images "
                "and 33 campaign films; 18 purposively selected discursive prototypes for "
                "intensive analysis. Methodology combined Barthesian visual semiotics, "
                "Laclau/Mouffe discourse theory, a structured codebook, Excel-based coding "
                "matrices, and intra-coder reliability checks."
            ),
            "motivation": (
                "Comparative study of Reformist and Principlist visual discourse in "
                "Iranian presidential campaigns from 1997 to 2017."
            ),
            "problems": "",
            "research_questions": "",
            "methods": (
                "Corpus of 80 images and 33 campaign films; 18 purposively selected "
                "discursive prototypes for intensive analysis. Methodology combined "
                "Barthesian visual semiotics, Laclau/Mouffe discourse theory, a structured "
                "codebook, Excel-based coding matrices, and intra-coder reliability checks."
            ),
            "future_directions": "",
        },
    ),
    "fa": (
        {
            "slug": "pars-sql-vtd-edge",
            "title": "PARS-SQL / VTD-Edge",
            "summary": (
                "سامانه‌ای محلی و حریم‌خصوصی‌محور برای Text-to-SQL فارسی در تحلیل "
                "سلامت روان و سبک زندگی. از فارسی محاوره‌ای، خطاهای تایپی، Finglish، "
                "ترکیب فارسی و انگلیسی، عبارت‌های تاریخ Jalali، مدیریت ابهام، مسیردهی "
                "پرس‌وجوهای ناایمن، اعتبارسنجی، خودداری از پاسخ و خروجی توضیح‌پذیر "
                "پشتیبانی می‌کند. زمان‌اجرای پژوهشی و زمان‌اجرای edge هستهٔ مشترکی برای "
                "نرمال‌سازی، پیونددهی، اعتبارسنجی، اجرا و قالب‌بندی دارند. برنامهٔ ارزیابی، "
                "قابلیت اعتماد، ایمنی، استحکام، تأخیر، حافظه، مستندسازی مجموعه‌داده، "
                "توافق انسانی و تکرارپذیری را در اولویت قرار می‌دهد."
            ),
            "motivation": (
                "سامانه‌ای محلی و حریم‌خصوصی‌محور برای Text-to-SQL فارسی در تحلیل "
                "سلامت روان و سبک زندگی."
            ),
            "problems": (
                "پشتیبانی از فارسی محاوره‌ای، خطاهای تایپی، Finglish، ترکیب فارسی و "
                "انگلیسی، عبارت‌های تاریخ Jalali، مدیریت ابهام، مسیردهی پرس‌وجوهای "
                "ناایمن، اعتبارسنجی، خودداری از پاسخ و خروجی توضیح‌پذیر."
            ),
            "research_questions": "",
            "methods": (
                "زمان‌اجرای پژوهشی و زمان‌اجرای edge هستهٔ مشترکی برای نرمال‌سازی، "
                "پیونددهی، اعتبارسنجی، اجرا و قالب‌بندی دارند."
            ),
            "future_directions": (
                "برنامهٔ ارزیابی، قابلیت اعتماد، ایمنی، استحکام، تأخیر، حافظه، "
                "مستندسازی مجموعه‌داده، توافق انسانی و تکرارپذیری را در اولویت قرار "
                "می‌دهد."
            ),
        },
        {
            "slug": "story-driven-dashboard-design",
            "title": "چارچوب طراحی داشبورد روایت‌محور",
            "summary": (
                "چارچوبی مبتنی بر علم طراحی برای تبدیل پنل‌های ایستای KPI به "
                "سامانه‌های روایت‌محورِ پشتیبان تصمیم. این چارچوب روایت‌گری داده، DIKW، "
                "SMART/GQM/GQMD، معماری داده، ادراک بصری، بار شناختی، HCI، تعامل، "
                "ارزیابی، آموزش، حکمرانی و تحلیل تقویت‌شده با AI را یکپارچه می‌کند. "
                "در یک مطالعهٔ موردی از ادارهٔ عمومی در سطح ملی، روی 9 مجموعهٔ داشبورد "
                "سازمانی به کار گرفته شد."
            ),
            "motivation": (
                "چارچوبی مبتنی بر علم طراحی برای تبدیل پنل‌های ایستای KPI به "
                "سامانه‌های روایت‌محورِ پشتیبان تصمیم."
            ),
            "problems": "",
            "research_questions": "",
            "methods": (
                "یکپارچه‌سازی روایت‌گری داده، DIKW، SMART/GQM/GQMD، معماری داده، "
                "ادراک بصری، بار شناختی، HCI، تعامل، ارزیابی، آموزش، حکمرانی و تحلیل "
                "تقویت‌شده با AI."
            ),
            "future_directions": (
                "در یک مطالعهٔ موردی از ادارهٔ عمومی در سطح ملی، روی 9 مجموعهٔ "
                "داشبورد سازمانی به کار گرفته شد."
            ),
        },
        {
            "slug": "visual-political-communication",
            "title": "پژوهش ارتباطات سیاسی بصری",
            "summary": (
                "مطالعه‌ای تطبیقی دربارهٔ گفتمان بصری اصلاح‌طلبان و اصول‌گرایان در "
                "کارزارهای انتخاباتی ریاست‌جمهوری ایران از 1997 تا 2017. پیکره شامل "
                "80 تصویر و 33 فیلم انتخاباتی بود؛ 18 نمونهٔ گفتمانیِ هدفمند برای "
                "تحلیل عمیق انتخاب شد. روش‌شناسی، نشانه‌شناسی بصریِ Barthesian، "
                "نظریهٔ گفتمان Laclau/Mouffe، کتابچهٔ کدگذاری ساختاریافته، "
                "ماتریس‌های کدگذاری مبتنی بر Excel و بررسی‌های پایایی درون‌کدگذار را "
                "ترکیب کرد."
            ),
            "motivation": (
                "مطالعه‌ای تطبیقی دربارهٔ گفتمان بصری اصلاح‌طلبان و اصول‌گرایان در "
                "کارزارهای انتخاباتی ریاست‌جمهوری ایران از 1997 تا 2017."
            ),
            "problems": "",
            "research_questions": "",
            "methods": (
                "پیکره شامل 80 تصویر و 33 فیلم انتخاباتی؛ 18 نمونهٔ گفتمانی هدفمند "
                "برای تحلیل عمیق. روش‌شناسی، نشانه‌شناسی بصری Barthesian، نظریهٔ "
                "گفتمان Laclau/Mouffe، کتابچهٔ کدگذاری ساختاریافته، ماتریس‌های "
                "کدگذاری Excel و بررسی‌های پایایی درون‌کدگذار."
            ),
            "future_directions": "",
        },
    ),
}

PUBLICATIONS: dict[str, tuple[PublicationSeed, ...]] = {
    "en": (
        {
            "slug": "visual-discourse-presidential-elections",
            "title": (
                "A Comparative Analysis of Reformist and Principlist Visual Discourses "
                "in Presidential Elections (1997–2017)"
            ),
            "authors": "Mohammadi, T., & Emamifar, S. N.",
            "venue": "Manuscript in final revision",
        },
        {
            "slug": "dashboard-design-data-storytelling",
            "title": (
                "Guidelines for Designing Information Dashboards Using Data Storytelling "
                "Methodology"
            ),
            "authors": "Mohammadi, T.",
            "venue": (
                "Persian book manuscript, approximately 330 pages and 12 chapters; "
                "second editorial revision in preparation for publication"
            ),
        },
        {
            "slug": "vtd-edge-persian-nlp-to-sql",
            "title": (
                "VTD-Edge: Reliable, Privacy-Preserving Persian NLP-to-SQL on "
                "Resource-Constrained Devices"
            ),
            "authors": "Mohammadi, T.",
            "venue": "Technical manuscript in preparation",
        },
    ),
    "fa": (
        {
            "slug": "visual-discourse-presidential-elections",
            "title": (
                "تحلیلی تطبیقی از گفتمان‌های بصری اصلاح‌طلبان و اصول‌گرایان در "
                "انتخابات ریاست‌جمهوری (1997–2017)"
            ),
            "authors": "Mohammadi, T., & Emamifar, S. N.",
            "venue": "دست‌نوشته در مرحلهٔ بازبینی نهایی",
        },
        {
            "slug": "dashboard-design-data-storytelling",
            "title": (
                "راهنمای طراحی داشبوردهای اطلاعاتی با استفاده از روش‌شناسی "
                "روایت‌گری داده"
            ),
            "authors": "Mohammadi, T.",
            "venue": (
                "دست‌نوشتهٔ کتاب فارسی، در حدود 330 صفحه و 12 فصل؛ بازبینی ویراستاری "
                "دوم برای آماده‌سازی انتشار در دست انجام است"
            ),
        },
        {
            "slug": "vtd-edge-persian-nlp-to-sql",
            "title": (
                "VTD-Edge: NLP-to-SQL فارسیِ قابل‌اعتماد و حافظ حریم خصوصی روی "
                "دستگاه‌های با منابع محدود"
            ),
            "authors": "Mohammadi, T.",
            "venue": "دست‌نوشتهٔ فنی در دست تهیه",
        },
    ),
}

PROJECTS: dict[str, tuple[ProjectSeed, ...]] = {
    "en": (
        {
            "slug": "pars-sql-vtd-edge",
            "title": "PARS-SQL / VTD-Edge",
            "project_type": "ai",
            "objective": (
                "Local, privacy-first Persian Text-to-SQL system for mental-health and "
                "lifestyle analytics."
            ),
            "methods_summary": (
                "Research runtime and edge runtime share a common normalization, linking, "
                "validation, execution, and formatting core."
            ),
            "role": "",
            "license": "cc-by-nc-4",
            "code_availability": "public",
            "data_availability": "not_available",
            "demo_availability": "not_available",
            "code_url": "https://github.com/tahamohamadi-ir/ADHD-VTD",
            "topic_slugs": ("pars-sql-vtd-edge",),
            "publication_slugs": ("vtd-edge-persian-nlp-to-sql",),
        },
        {
            "slug": "story-driven-dashboard-design",
            "title": "Story-Driven Dashboard Design Framework",
            "project_type": "design",
            "objective": (
                "Design-science framework for transforming static KPI panels into "
                "narrative decision-support systems."
            ),
            "methods_summary": (
                "Integrates data storytelling, DIKW, SMART/GQM/GQMD, data architecture, "
                "visual perception, cognitive load, HCI, interaction, evaluation, training, "
                "governance, and AI-enhanced analytics."
            ),
            "role": "",
            "license": "all-rights-reserved",
            "code_availability": "not_applicable",
            "data_availability": "not_applicable",
            "demo_availability": "not_applicable",
            "code_url": "",
            "topic_slugs": ("story-driven-dashboard-design",),
            "publication_slugs": ("dashboard-design-data-storytelling",),
        },
        {
            "slug": "visual-political-communication",
            "title": "Visual Political Communication Research",
            "project_type": "research",
            "objective": (
                "Comparative study of Reformist and Principlist visual discourse in "
                "Iranian presidential campaigns from 1997 to 2017."
            ),
            "methods_summary": (
                "Methodology combined Barthesian visual semiotics, Laclau/Mouffe discourse "
                "theory, a structured codebook, Excel-based coding matrices, and "
                "intra-coder reliability checks."
            ),
            "role": "",
            "license": "all-rights-reserved",
            "code_availability": "not_applicable",
            "data_availability": "not_applicable",
            "demo_availability": "not_applicable",
            "code_url": "",
            "topic_slugs": ("visual-political-communication",),
            "publication_slugs": ("visual-discourse-presidential-elections",),
        },
    ),
    "fa": (
        {
            "slug": "pars-sql-vtd-edge",
            "title": "PARS-SQL / VTD-Edge",
            "project_type": "ai",
            "objective": (
                "سامانه‌ای محلی و حریم‌خصوصی‌محور برای Text-to-SQL فارسی در تحلیل "
                "سلامت روان و سبک زندگی."
            ),
            "methods_summary": (
                "زمان‌اجرای پژوهشی و زمان‌اجرای edge هستهٔ مشترکی برای نرمال‌سازی، "
                "پیونددهی، اعتبارسنجی، اجرا و قالب‌بندی دارند."
            ),
            "role": "",
            "license": "cc-by-nc-4",
            "code_availability": "public",
            "data_availability": "not_available",
            "demo_availability": "not_available",
            "code_url": "https://github.com/tahamohamadi-ir/ADHD-VTD",
            "topic_slugs": ("pars-sql-vtd-edge",),
            "publication_slugs": ("vtd-edge-persian-nlp-to-sql",),
        },
        {
            "slug": "story-driven-dashboard-design",
            "title": "چارچوب طراحی داشبورد روایت‌محور",
            "project_type": "design",
            "objective": (
                "چارچوبی مبتنی بر علم طراحی برای تبدیل پنل‌های ایستای KPI به "
                "سامانه‌های روایت‌محورِ پشتیبان تصمیم."
            ),
            "methods_summary": (
                "این چارچوب روایت‌گری داده، DIKW، SMART/GQM/GQMD، معماری داده، "
                "ادراک بصری، بار شناختی، HCI، تعامل، ارزیابی، آموزش، حکمرانی و "
                "تحلیل تقویت‌شده با AI را یکپارچه می‌کند."
            ),
            "role": "",
            "license": "all-rights-reserved",
            "code_availability": "not_applicable",
            "data_availability": "not_applicable",
            "demo_availability": "not_applicable",
            "code_url": "",
            "topic_slugs": ("story-driven-dashboard-design",),
            "publication_slugs": ("dashboard-design-data-storytelling",),
        },
        {
            "slug": "visual-political-communication",
            "title": "پژوهش ارتباطات سیاسی بصری",
            "project_type": "research",
            "objective": (
                "مطالعه‌ای تطبیقی دربارهٔ گفتمان بصری اصلاح‌طلبان و اصول‌گرایان در "
                "کارزارهای انتخاباتی ریاست‌جمهوری ایران از 1997 تا 2017."
            ),
            "methods_summary": (
                "روش‌شناسی، نشانه‌شناسی بصریِ Barthesian، نظریهٔ گفتمان Laclau/Mouffe، "
                "کتابچهٔ کدگذاری ساختاریافته، ماتریس‌های کدگذاری مبتنی بر Excel و "
                "بررسی‌های پایایی درون‌کدگذار را ترکیب کرد."
            ),
            "role": "",
            "license": "all-rights-reserved",
            "code_availability": "not_applicable",
            "data_availability": "not_applicable",
            "demo_availability": "not_applicable",
            "code_url": "",
            "topic_slugs": ("visual-political-communication",),
            "publication_slugs": ("visual-discourse-presidential-elections",),
        },
    ),
}

ARTICLES: dict[str, tuple[ArticleSeed, ...]] = {
    "en": (
        {
            "slug": "pars-sql-vtd-edge-overview",
            "title": "PARS-SQL / VTD-Edge: Privacy-First Persian NLP-to-SQL",
            "excerpt": (
                "Why local, privacy-first Persian Text-to-SQL matters for mental-health "
                "and lifestyle analytics — and how PARS-SQL / VTD-Edge approaches robustness, "
                "safety, and edge deployment."
            ),
            "body": (
                "<p>Local, privacy-first Persian Text-to-SQL system for mental-health and "
                "lifestyle analytics. Supports colloquial Persian, typos, Finglish, "
                "mixed Persian-English, Jalali date expressions, ambiguity handling, "
                "unsafe-query routing, validation, abstention, and explainable output.</p>"
                "<h2>Shared core</h2>"
                "<p>Research runtime and edge runtime share a common normalization, linking, "
                "validation, execution, and formatting core.</p>"
                "<h2>Evaluation priorities</h2>"
                "<p>Reliability, safety, robustness, latency, memory, dataset documentation, "
                "human agreement, and reproducibility.</p>"
            ),
            "license": "cc-by-nc-4",
        },
        {
            "slug": "story-driven-dashboard-design-intro",
            "title": "Story-Driven Dashboard Design: From KPI Panels to Narrative Decisions",
            "excerpt": (
                "A design-science framework for transforming static KPI panels into "
                "narrative decision-support systems — applied in national public-administration "
                "dashboard programmes."
            ),
            "body": (
                "<p>Design-science framework for transforming static KPI panels into "
                "narrative decision-support systems. Integrates data storytelling, DIKW, "
                "SMART/GQM/GQMD, data architecture, visual perception, cognitive load, HCI, "
                "interaction, evaluation, training, governance, and AI-enhanced analytics.</p>"
                "<p>Applied in a national public-administration case with nine organizational "
                "dashboard suites.</p>"
            ),
            "license": "cc-by-nc-4",
        },
    ),
    "fa": (
        {
            "slug": "pars-sql-vtd-edge-overview",
            "title": "PARS-SQL / VTD-Edge: Text-to-SQL فارسی محلی و حریم‌خصوصی‌محور",
            "excerpt": (
                "چرا Text-to-SQL فارسی محلی و حریم‌خصوصی‌محور برای تحلیل سلامت روان و "
                "سبک زندگی اهمیت دارد — و PARS-SQL / VTD-Edge چگونه استحکام، ایمنی و "
                "استقرار edge را دنبال می‌کند."
            ),
            "body": (
                "<p>سامانهٔ Text-to-SQL فارسی محلی و حریم‌خصوصی‌محور برای تحلیل سلامت "
                "روان و سبک زندگی.</p>"
                "<h2>هستهٔ مشترک</h2>"
                "<p>زمان اجرای پژوهشی و edge هستهٔ مشترکی از نرمال‌سازی، پیوند، "
                "اعتبارسنجی، اجرا و قالب‌بندی دارند.</p>"
            ),
            "license": "cc-by-nc-4",
        },
        {
            "slug": "story-driven-dashboard-design-intro",
            "title": "طراحی داشبورد روایت‌محور: از پنل KPI تا تصمیم روایی",
            "excerpt": (
                "چارچوب علم طراحی برای تبدیل پنل‌های KPI ایستا به سامانه‌های "
                "پشتیبان تصمیم روایی."
            ),
            "body": (
                "<p>چارچوب علم طراحی برای تبدیل پنل‌های KPI ایستا به سامانه‌های "
                "پشتیبان تصمیم روایی.</p>"
            ),
            "license": "cc-by-nc-4",
        },
    ),
}
