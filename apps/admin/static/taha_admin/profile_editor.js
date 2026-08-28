(function profileEditorApp() {
  const bootstrapNode = document.getElementById("profile-editor-bootstrap");
  const root = document.querySelector("[data-profile-editor-root]");
  if (!bootstrapNode || !root) {
    return;
  }

  const bootstrap = JSON.parse(bootstrapNode.textContent);
  const language = {
    en: {
      back: "Back to profiles",
      localeTabsTitle: "Locale tabs",
      localeTabsMeta: "Switch only through real sibling records. Missing locales stay explicit.",
      statePanelTitle: "State panel",
      statePanelMeta: "Revision-aware writes only. Stale saves must reload first.",
      translation: "Translation",
      lifecycle: "Lifecycle",
      revision: "Revision",
      publishedAt: "Published at",
      availableLocales: "Available locales",
      save: "Save current status",
      draft: "Save as draft",
      review: "Send to review",
      publish: "Publish",
      archive: "Archive",
      reload: "Reload latest",
      confirmPublish: "Publish this locale now?",
      confirmArchive: "Archive this locale now?",
      saveSuccess: "Profile saved successfully.",
      conflictTitle: "Revision conflict",
      conflictMessage:
        "This record changed in another session. Reload the latest revision before saving again.",
      validationTitle: "Validation error",
      networkTitle: "Request failed",
      title: "Title",
      slug: "Slug",
      seoTitle: "SEO title",
      seoDescription: "SEO description",
      shortBio: "Short bio",
      longBio: "Long bio",
      availability: "Availability",
      lifecycleHint: "Use publish/archive confirmations for visible lifecycle changes.",
      sectionSkills: "Skills",
      sectionExperience: "Experience",
      sectionEducation: "Education",
      sectionPublications: "Publications",
      sectionResearchProjects: "Research projects",
      sectionCertificates: "Certificates",
      sectionSocials: "Social links",
      addRow: "Add row",
      removeRow: "Remove",
      emptyRows: "No rows yet.",
      checklistTitle: "Title",
      checklistSlug: "Slug",
      checklistBody: "Body",
      checklistSeo: "SEO",
      current: "Current",
      fieldCategory: "Category",
      fieldName: "Name",
      fieldSource: "Source",
      fieldOrganization: "Organization",
      fieldRole: "Role",
      fieldPeriod: "Period",
      fieldLocation: "Location",
      fieldWebsite: "Website",
      fieldBullets: "Bullets",
      fieldInstitution: "Institution",
      fieldDegree: "Degree",
      fieldField: "Field",
      fieldGpa: "GPA",
      fieldThesis: "Thesis",
      fieldStatus: "Status",
      fieldSummary: "Summary",
      fieldUrl: "URL",
      fieldLinkLabel: "Link label",
      fieldDetail: "Detail",
      fieldDetailBody: "Detail body",
      fieldTranslationKey: "Translation key",
      detailRouteHint: "Latin slug required when detail body is set.",
      fieldPlatform: "Platform",
      bulletsHint: "One bullet per line.",
      createLocale: "Create draft locale",
      creatingLocale: "Creating locale…",
      missingLocaleHint:
        "The alternate locale is missing.",
    },
    fa: {
      back: "بازگشت به فهرست پروفایل‌ها",
      localeTabsTitle: "تب‌های زبان",
      localeTabsMeta: "فقط از مسیر رکوردهای واقعی جابه‌جا شوید؛ زبان گمشده باید صریح بماند.",
      statePanelTitle: "پنل وضعیت",
      statePanelMeta: "ذخیره فقط با revision جاری مجاز است؛ ذخیرهٔ کهنه باید اول بازخوانی شود.",
      translation: "ترجمه",
      lifecycle: "چرخهٔ انتشار",
      revision: "بازنگری",
      publishedAt: "زمان انتشار",
      availableLocales: "زبان‌های موجود",
      save: "ذخیره با وضعیت فعلی",
      draft: "ذخیره به‌صورت پیش‌نویس",
      review: "ارسال برای بازبینی",
      publish: "انتشار",
      archive: "بایگانی",
      reload: "بارگذاری آخرین نسخه",
      confirmPublish: "این زبان اکنون منتشر شود؟",
      confirmArchive: "این زبان بایگانی شود؟",
      saveSuccess: "پروفایل با موفقیت ذخیره شد.",
      conflictTitle: "تعارض بازنگری",
      conflictMessage:
        "این رکورد در نشست دیگری تغییر کرده است. قبل از ذخیرهٔ دوباره آخرین بازنگری را بارگذاری کنید.",
      validationTitle: "خطای اعتبارسنجی",
      networkTitle: "درخواست ناموفق بود",
      title: "عنوان",
      slug: "اسلاگ",
      seoTitle: "عنوان SEO",
      seoDescription: "توضیح SEO",
      shortBio: "بیوی کوتاه",
      longBio: "بیوی بلند",
      availability: "وضعیت دسترس‌پذیری",
      lifecycleHint: "برای تغییرهای قابل‌مشاهدهٔ چرخهٔ انتشار از تأیید انتشار/بایگانی استفاده کنید.",
      sectionSkills: "مهارت‌ها",
      sectionExperience: "تجربه‌ها",
      sectionEducation: "تحصیلات",
      sectionPublications: "انتشارات",
      sectionResearchProjects: "پروژه‌های پژوهشی",
      sectionCertificates: "گواهی‌ها",
      sectionSocials: "پیوندهای اجتماعی",
      addRow: "افزودن ردیف",
      removeRow: "حذف",
      emptyRows: "هنوز ردیفی ثبت نشده است.",
      checklistTitle: "عنوان",
      checklistSlug: "اسلاگ",
      checklistBody: "بدنه",
      checklistSeo: "SEO",
      current: "جاری",
      fieldCategory: "دسته",
      fieldName: "نام",
      fieldSource: "منبع",
      fieldOrganization: "سازمان",
      fieldRole: "نقش",
      fieldPeriod: "بازه",
      fieldLocation: "مکان",
      fieldWebsite: "وب‌سایت",
      fieldBullets: "بولت‌ها",
      fieldInstitution: "مؤسسه",
      fieldDegree: "مدرک",
      fieldField: "رشته",
      fieldGpa: "معدل",
      fieldThesis: "پایان‌نامه",
      fieldStatus: "وضعیت",
      fieldSummary: "خلاصه",
      fieldUrl: "نشانی",
      fieldLinkLabel: "برچسب پیوند",
      fieldDetail: "جزئیات",
      fieldDetailBody: "متن جزئیات",
      fieldTranslationKey: "کلید ترجمه",
      detailRouteHint: "اگر متن جزئیات پر است، slug لاتین الزامی است.",
      fieldPlatform: "پلتفرم",
      bulletsHint: "هر بولت را در یک خط جدا وارد کنید.",
      createLocale: "ایجاد زبان پیش‌نویس",
      creatingLocale: "در حال ایجاد زبان…",
      missingLocaleHint:
        "زبان مقابل هنوز وجود ندارد.",
    },
  };

  const detailRouteFields = [
    { name: "slug", labelKey: "slug", type: "text", hintKey: "detailRouteHint" },
    { name: "translationKey", labelKey: "fieldTranslationKey", type: "text" },
    { name: "detailBody", labelKey: "fieldDetailBody", type: "textarea" },
  ];

  const sectionDefinitions = [
    {
      key: "skills",
      titleKey: "sectionSkills",
      fields: [
        { name: "category", labelKey: "fieldCategory", type: "text" },
        { name: "name", labelKey: "fieldName", type: "text" },
        { name: "source", labelKey: "fieldSource", type: "text" },
        ...detailRouteFields,
      ],
    },
    {
      key: "experience",
      titleKey: "sectionExperience",
      fields: [
        { name: "organization", labelKey: "fieldOrganization", type: "text" },
        { name: "role", labelKey: "fieldRole", type: "text" },
        { name: "period", labelKey: "fieldPeriod", type: "text" },
        { name: "location", labelKey: "fieldLocation", type: "text" },
        { name: "website", labelKey: "fieldWebsite", type: "url" },
        {
          name: "bullets",
          labelKey: "fieldBullets",
          type: "textarea",
          hintKey: "bulletsHint",
          fromRow(value) {
            return Array.isArray(value) ? value.join("\n") : "";
          },
          toRow(value) {
            return value
              .split("\n")
              .map((item) => item.trim())
              .filter(Boolean);
          },
        },
        ...detailRouteFields,
      ],
    },
    {
      key: "education",
      titleKey: "sectionEducation",
      fields: [
        { name: "institution", labelKey: "fieldInstitution", type: "text" },
        { name: "degree", labelKey: "fieldDegree", type: "text" },
        { name: "field", labelKey: "fieldField", type: "text" },
        { name: "period", labelKey: "fieldPeriod", type: "text" },
        { name: "gpa", labelKey: "fieldGpa", type: "text" },
        { name: "thesis", labelKey: "fieldThesis", type: "textarea" },
        ...detailRouteFields,
      ],
    },
    {
      key: "publications",
      titleKey: "sectionPublications",
      fields: [
        { name: "title", labelKey: "title", type: "text" },
        { name: "status", labelKey: "fieldStatus", type: "text" },
        ...detailRouteFields,
      ],
    },
    {
      key: "researchProjects",
      titleKey: "sectionResearchProjects",
      fields: [
        { name: "title", labelKey: "title", type: "text" },
        { name: "summary", labelKey: "fieldSummary", type: "textarea" },
        { name: "url", labelKey: "fieldUrl", type: "url" },
        { name: "linkLabel", labelKey: "fieldLinkLabel", type: "text" },
        ...detailRouteFields,
      ],
    },
    {
      key: "certificates",
      titleKey: "sectionCertificates",
      fields: [
        { name: "name", labelKey: "fieldName", type: "text" },
        { name: "detail", labelKey: "fieldDetail", type: "text" },
        ...detailRouteFields,
      ],
    },
    {
      key: "socials",
      titleKey: "sectionSocials",
      fields: [
        { name: "platform", labelKey: "fieldPlatform", type: "text" },
        { name: "url", labelKey: "fieldUrl", type: "url" },
      ],
    },
  ];

  const adminHttp = createAdminHttp(bootstrap.csrfToken);
  let state = {
    profile: bootstrap.profile,
    localeTabs: bootstrap.localeTabs,
    flash: null,
    error: null,
    saving: false,
    creatingLocale: null,
  };

  function copy() {
    return language[state.profile.locale] || language.en;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function createAdminHttp(csrfToken) {
    async function request(url, options = {}) {
      const headers = {
        Accept: "application/json",
        ...options.headers,
      };
      if (options.body !== undefined) {
        headers["Content-Type"] = "application/json";
      }
      if (options.ifMatch !== undefined) {
        headers["If-Match"] = String(options.ifMatch);
      }
      if (csrfToken) {
        headers["X-CSRFToken"] = csrfToken;
      }

      const response = await fetch(url, {
        method: options.method || "GET",
        credentials: "same-origin",
        headers,
        body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      });
      const contentType = response.headers.get("content-type") || "";
      const payload = contentType.includes("application/json")
        ? await response.json()
        : { detail: await response.text() };
      if (!response.ok) {
        throw normalizeError(response.status, payload);
      }
      return payload;
    }

    return {
      getJson(url) {
        return request(url);
      },
      postJson(url, body) {
        return request(url, { method: "POST", body });
      },
      putJson(url, body, ifMatch) {
        return request(url, { method: "PUT", body, ifMatch });
      },
    };
  }

  function normalizeError(status, payload) {
    const detail =
      typeof payload?.detail === "string" ? payload.detail : "The request failed.";
    const fieldErrors =
      payload && typeof payload.detail === "object" && !Array.isArray(payload.detail)
        ? payload.detail
        : null;
    return {
      status,
      code: payload?.code || "HTTP_ERROR",
      detail,
      fieldErrors,
      currentRevision: payload?.currentRevision,
    };
  }

  function statusOptionsHtml(selected) {
    const options = [
      ["draft", copy().draft],
      ["review", copy().review],
      ["published", copy().publish],
      ["archived", copy().archive],
    ];
    return options
      .map(
        ([value, label]) =>
          `<option value="${value}" ${selected === value ? "selected" : ""}>${escapeHtml(
            label
          )}</option>`
      )
      .join("");
  }

  function checklistHtml(checklist) {
    const strings = copy();
    const rows = [
      [strings.checklistTitle, checklist?.title],
      [strings.checklistSlug, checklist?.slug],
      [strings.checklistBody, checklist?.body],
      [strings.checklistSeo, checklist?.seo],
    ];
    return `
      <div class="tp-completeness">
        ${rows
          .map(
            ([label, complete]) =>
              `<span class="tp-completeness__item ${
                complete ? "is-complete" : ""
              }">${escapeHtml(label)}</span>`
          )
          .join("")}
      </div>
    `;
  }

  function renderLocaleTabs() {
    const strings = copy();
    return `
      <aside class="tp-editor-panel">
        <h2 class="tp-editor-panel__title">${escapeHtml(strings.localeTabsTitle)}</h2>
        <p class="tp-editor-panel__meta">${escapeHtml(strings.localeTabsMeta)}</p>
        <div class="tp-editor-locale-list">
          ${state.localeTabs
            .map((tab) => {
              const href = tab.href ? escapeHtml(tab.href) : "#";
              const label =
                tab.badge === "MISSING" ? strings.missingLocaleHint : escapeHtml(tab.badge);
              return `
                <section class="tp-editor-locale-item ${tab.isCurrent ? "is-current" : ""}" dir="${
                  tab.locale === "fa" ? "rtl" : "ltr"
                }">
                  <div class="tp-editor-locale-item__heading">
                    ${
                      tab.href
                        ? `<a class="tp-editor-locale-link" href="${href}">${escapeHtml(
                            tab.label
                          )}</a>`
                        : `<span class="tp-editor-locale-link" aria-disabled="true">${escapeHtml(
                            tab.label
                          )}</span>`
                    }
                    <span class="tp-badge tp-badge--muted">${
                      tab.isCurrent ? escapeHtml(strings.current) : escapeHtml(tab.badge)
                    }</span>
                  </div>
                  ${checklistHtml(tab.completeness)}
                  ${
                    !tab.href && tab.createUrl
                      ? `
                        <div class="tp-editor-stack">
                          <p class="tp-inline-hint">${escapeHtml(strings.missingLocaleHint)}</p>
                          <button
                            type="button"
                            class="button button-small button-secondary"
                            data-action="create-locale"
                            data-create-url="${escapeHtml(tab.createUrl)}"
                            data-target-locale="${escapeHtml(tab.locale)}"
                            ${state.creatingLocale === tab.locale ? "disabled" : ""}
                          >
                            ${escapeHtml(
                              state.creatingLocale === tab.locale
                                ? strings.creatingLocale
                                : strings.createLocale
                            )}
                          </button>
                        </div>
                      `
                      : !tab.href
                        ? `<p class="tp-inline-hint">${escapeHtml(strings.missingLocaleHint)}</p>`
                      : `<p class="tp-inline-hint">${escapeHtml(label)}</p>`
                  }
                </section>
              `;
            })
            .join("")}
        </div>
      </aside>
    `;
  }

  function renderStatePanel() {
    const strings = copy();
    const profile = state.profile;
    const availableLocales = (profile.translationStatus?.availableLocales || []).join(", ");
    return `
      <aside class="tp-editor-panel">
        <h2 class="tp-editor-panel__title">${escapeHtml(strings.statePanelTitle)}</h2>
        <p class="tp-editor-panel__meta">${escapeHtml(strings.statePanelMeta)}</p>
        <div class="tp-editor-state-list">
          <div class="tp-editor-state-row">
            <span>${escapeHtml(strings.translation)}</span>
            <span class="tp-badge tp-badge--muted">${escapeHtml(
              profile.translationStatus?.status || "MISSING"
            )}</span>
          </div>
          <div class="tp-editor-state-row">
            <span>${escapeHtml(strings.lifecycle)}</span>
            <span class="tp-badge">${escapeHtml(profile.status)}</span>
          </div>
          <div class="tp-editor-state-row">
            <span>${escapeHtml(strings.revision)}</span>
            <strong>${escapeHtml(profile.revision)}</strong>
          </div>
          <div class="tp-editor-state-row">
            <span>${escapeHtml(strings.publishedAt)}</span>
            <span>${escapeHtml(profile.publishedAt || "—")}</span>
          </div>
          <div class="tp-editor-state-row">
            <span>${escapeHtml(strings.availableLocales)}</span>
            <span>${escapeHtml(availableLocales || profile.locale)}</span>
          </div>
        </div>
      </aside>
    `;
  }

  function inputForField(field, value) {
    const strings = copy();
    const actualValue = field.fromRow ? field.fromRow(value) : value || "";
    const id = `${field.name}-${Math.random().toString(36).slice(2, 8)}`;
    const baseAttrs = `id="${id}" data-field-name="${field.name}" class="${
      field.type === "textarea" ? "tp-editor-textarea" : "tp-editor-input"
    }"`;
    const hint = field.hintKey
      ? `<p class="tp-inline-hint">${escapeHtml(strings[field.hintKey])}</p>`
      : "";
    if (field.type === "textarea") {
      return `
        <div class="tp-editor-field--full">
          <label for="${id}">${escapeHtml(strings[field.labelKey])}</label>
          <textarea ${baseAttrs}>${escapeHtml(actualValue)}</textarea>
          ${hint}
        </div>
      `;
    }
    return `
      <div class="tp-editor-field">
        <label for="${id}">${escapeHtml(strings[field.labelKey])}</label>
        <input type="${field.type}" value="${escapeHtml(actualValue)}" ${baseAttrs}>
      </div>
    `;
  }

  function renderSection(section) {
    const strings = copy();
    const rows = state.profile[section.key] || [];
    return `
      <section class="tp-editor-section">
        <header class="tp-editor-section__body">
          <div>
            <h3 class="tp-editor-section__title">${escapeHtml(strings[section.titleKey])}</h3>
          </div>
          <div>
            <button type="button" class="button button-small button-secondary" data-action="add-row" data-section="${section.key}">
              ${escapeHtml(strings.addRow)}
            </button>
          </div>
        </header>
        <div class="tp-editor-repeater" data-section-body="${section.key}">
          ${
            rows.length
              ? rows
                  .map(
                    (row, index) => `
                    <article class="tp-editor-repeater-card" data-row-index="${index}">
                      <div class="tp-editor-repeater-card__header">
                        <strong>${escapeHtml(strings[section.titleKey])} ${index + 1}</strong>
                        <button type="button" class="button button-small button-secondary" data-action="remove-row" data-section="${section.key}" data-index="${index}">
                          ${escapeHtml(strings.removeRow)}
                        </button>
                      </div>
                      <div class="tp-editor-fields">
                        ${section.fields.map((field) => inputForField(field, row[field.name])).join("")}
                      </div>
                    </article>
                  `
                  )
                  .join("")
              : `<p class="tp-empty-note">${escapeHtml(strings.emptyRows)}</p>`
          }
        </div>
      </section>
    `;
  }

  function renderFlash() {
    if (state.error) {
      const title =
        state.error.code === "REVISION_CONFLICT"
          ? copy().conflictTitle
          : state.error.code === "VALIDATION_ERROR"
            ? copy().validationTitle
            : copy().networkTitle;
      const body =
        state.error.code === "REVISION_CONFLICT"
          ? copy().conflictMessage
          : state.error.detail || copy().validationTitle;
      const errorList = state.error.fieldErrors
        ? `<ul class="tp-editor-callout__list">${Object.entries(state.error.fieldErrors)
            .map(
              ([field, message]) =>
                `<li><strong>${escapeHtml(field)}</strong>: ${escapeHtml(
                  Array.isArray(message) ? message.join(", ") : message
                )}</li>`
            )
            .join("")}</ul>`
        : "";
      return `
        <section class="tp-editor-callout is-error">
          <h3 class="tp-editor-callout__title">${escapeHtml(title)}</h3>
          <p>${escapeHtml(body)}</p>
          ${errorList}
          ${
            state.error.code === "REVISION_CONFLICT"
              ? `<button type="button" class="button button-small button-secondary" data-action="reload-profile">${escapeHtml(
                  copy().reload
                )}</button>`
              : ""
          }
        </section>
      `;
    }
    if (state.flash) {
      return `
        <section class="tp-editor-callout is-success">
          <h3 class="tp-editor-callout__title">${escapeHtml(copy().saveSuccess)}</h3>
          <p>${escapeHtml(state.flash)}</p>
        </section>
      `;
    }
    return "";
  }

  function renderRootFields() {
    const strings = copy();
    return `
      <section class="tp-editor-section">
        <div class="tp-editor-fields">
          <div class="tp-editor-field">
            <label for="profile-title">${escapeHtml(strings.title)}</label>
            <input id="profile-title" class="tp-editor-input" data-root-field="title" value="${escapeHtml(
              state.profile.title
            )}">
          </div>
          <div class="tp-editor-field">
            <label for="profile-slug">${escapeHtml(strings.slug)}</label>
            <input id="profile-slug" class="tp-editor-input" data-root-field="slug" value="${escapeHtml(
              state.profile.slug
            )}">
          </div>
          <div class="tp-editor-field">
            <label for="profile-status">${escapeHtml(strings.lifecycle)}</label>
            <select id="profile-status" class="tp-editor-select" data-root-field="status">
              ${statusOptionsHtml(state.profile.status)}
            </select>
            <p class="tp-inline-hint">${escapeHtml(strings.lifecycleHint)}</p>
          </div>
          <div class="tp-editor-field--full">
            <label for="profile-seo-title">${escapeHtml(strings.seoTitle)}</label>
            <input id="profile-seo-title" class="tp-editor-input" data-root-field="seoTitle" value="${escapeHtml(
              state.profile.seoTitle || ""
            )}">
          </div>
          <div class="tp-editor-field--full">
            <label for="profile-seo-description">${escapeHtml(strings.seoDescription)}</label>
            <textarea id="profile-seo-description" class="tp-editor-textarea" data-root-field="seoDescription">${escapeHtml(
              state.profile.seoDescription || ""
            )}</textarea>
          </div>
          <div class="tp-editor-field--full">
            <label for="profile-short-bio">${escapeHtml(strings.shortBio)}</label>
            <textarea id="profile-short-bio" class="tp-editor-textarea" data-root-field="shortBio">${escapeHtml(
              state.profile.shortBio || ""
            )}</textarea>
          </div>
          <div class="tp-editor-field--full">
            <label for="profile-long-bio">${escapeHtml(strings.longBio)}</label>
            <textarea id="profile-long-bio" class="tp-editor-textarea" data-root-field="longBio">${escapeHtml(
              state.profile.longBio || ""
            )}</textarea>
          </div>
          <div class="tp-editor-field--full">
            <label for="profile-availability">${escapeHtml(strings.availability)}</label>
            <textarea id="profile-availability" class="tp-editor-textarea" data-root-field="availability">${escapeHtml(
              state.profile.availability || ""
            )}</textarea>
          </div>
        </div>
      </section>
    `;
  }

  function renderLifecycleActions() {
    const strings = copy();
    return `
      <section class="tp-editor-section">
        <div class="tp-editor-actions">
          <button type="button" class="button button-primary" data-action="save-profile" ${
            state.saving ? "disabled" : ""
          }>
            ${escapeHtml(strings.save)}
          </button>
          <button type="button" class="button button-secondary" data-action="save-profile" data-next-status="draft" ${
            state.saving ? "disabled" : ""
          }>
            ${escapeHtml(strings.draft)}
          </button>
          <button type="button" class="button button-secondary" data-action="save-profile" data-next-status="review" ${
            state.saving ? "disabled" : ""
          }>
            ${escapeHtml(strings.review)}
          </button>
          <button type="button" class="button button-secondary" data-action="save-profile" data-next-status="published" ${
            state.saving ? "disabled" : ""
          }>
            ${escapeHtml(strings.publish)}
          </button>
          <button type="button" class="button button-secondary" data-action="save-profile" data-next-status="archived" ${
            state.saving ? "disabled" : ""
          }>
            ${escapeHtml(strings.archive)}
          </button>
        </div>
      </section>
    `;
  }

  function render() {
    root.setAttribute("lang", state.profile.locale);
    root.setAttribute("dir", state.profile.locale === "fa" ? "rtl" : "ltr");
    root.innerHTML = `
      ${renderLocaleTabs()}
      <main class="tp-editor-main">
        ${renderFlash()}
        ${renderStatePanel()}
        <form data-profile-form>
          <div class="tp-editor-stack">
            ${renderRootFields()}
            ${renderLifecycleActions()}
            ${sectionDefinitions.map(renderSection).join("")}
          </div>
        </form>
      </main>
    `;
  }

  function emptyRowFor(section) {
    return Object.fromEntries(
      section.fields.map((field) => [field.name, field.type === "textarea" ? "" : ""])
    );
  }

  function syncLocaleTabs() {
    state.localeTabs = state.localeTabs.map((tab) => {
      if (tab.isCurrent) {
        return {
          ...tab,
          href: `/admin/profiles/${state.profile.locale}/${state.profile.slug}/`,
          completeness: inferCurrentCompleteness(),
        };
      }
      if (tab.locale === state.profile.translationStatus?.alternateLocale) {
        return {
          ...tab,
          badge: state.profile.translationStatus.status,
        };
      }
      return tab;
    });
  }

  function inferCurrentCompleteness() {
    return {
      title: Boolean(state.profile.title?.trim()),
      slug: Boolean(state.profile.slug?.trim()),
      body:
        Boolean(state.profile.shortBio?.trim() || state.profile.longBio?.trim()) &&
        sectionDefinitions.some((section) => (state.profile[section.key] || []).length > 0),
      seo: Boolean(state.profile.seoTitle?.trim() && state.profile.seoDescription?.trim()),
    };
  }

  function serializeForm() {
    const payload = {};
    root.querySelectorAll("[data-root-field]").forEach((field) => {
      payload[field.dataset.rootField] = field.value.trim();
    });
    sectionDefinitions.forEach((section) => {
      const rows = Array.from(root.querySelectorAll(`[data-section-body="${section.key}"] [data-row-index]`));
      payload[section.key] = rows.map((row) => {
        const result = {};
        const inputs = Array.from(row.querySelectorAll("[data-field-name]"));
        section.fields.forEach((field) => {
          const input = inputs.find((candidate) => candidate.dataset.fieldName === field.name);
          const rawValue = input ? input.value.trim() : "";
          result[field.name] = field.toRow ? field.toRow(rawValue) : rawValue;
        });
        return result;
      });
    });
    return payload;
  }

  async function reloadProfile() {
    state.saving = true;
    render();
    try {
      const data = await adminHttp.getJson(bootstrap.apiUrl);
      state.profile = data;
      state.error = null;
      state.flash = null;
      syncLocaleTabs();
    } catch (error) {
      state.error = error;
    } finally {
      state.saving = false;
      render();
    }
  }

  async function saveProfile(nextStatus) {
    const strings = copy();
    if (nextStatus === "published" && !window.confirm(strings.confirmPublish)) {
      return;
    }
    if (nextStatus === "archived" && !window.confirm(strings.confirmArchive)) {
      return;
    }

    const payload = serializeForm();
    payload.status = nextStatus || payload.status || state.profile.status;
    state.saving = true;
    state.error = null;
    state.flash = null;
    render();

    try {
      const data = await adminHttp.putJson(bootstrap.apiUrl, payload, state.profile.revision);
      state.profile = data;
      state.flash = strings.saveSuccess;
      syncLocaleTabs();
    } catch (error) {
      state.error = error;
      state.flash = null;
    } finally {
      state.saving = false;
      render();
    }
  }

  async function createSiblingLocale(url, targetLocale) {
    state.creatingLocale = targetLocale;
    state.error = null;
    state.flash = null;
    render();
    try {
      const data = await adminHttp.postJson(url, {});
      window.location.href = data.editorUrl;
    } catch (error) {
      state.error = error;
      state.creatingLocale = null;
      render();
    }
  }

  root.addEventListener("click", (event) => {
    const target = event.target.closest("[data-action]");
    if (!target) {
      return;
    }
    const { action, section, index, nextStatus } = target.dataset;
    if (action === "add-row") {
      const definition = sectionDefinitions.find((item) => item.key === section);
      if (!definition) {
        return;
      }
      state.profile[section] = [...(state.profile[section] || []), emptyRowFor(definition)];
      render();
      return;
    }
    if (action === "remove-row") {
      state.profile[section] = (state.profile[section] || []).filter(
        (_, rowIndex) => rowIndex !== Number(index)
      );
      render();
      return;
    }
    if (action === "save-profile") {
      saveProfile(nextStatus);
      return;
    }
    if (action === "create-locale") {
      createSiblingLocale(target.dataset.createUrl, target.dataset.targetLocale);
      return;
    }
    if (action === "reload-profile") {
      reloadProfile();
    }
  });

  render();
})();
