import { useEffect, useMemo, useState } from "react";

const ALL = "전체";

function formatBytes(bytes) {
  if (!bytes) return "—";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function Icon({ name, size = 20 }) {
  const paths = {
    search: <><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></>,
    close: <><path d="M18 6 6 18M6 6l12 12"/></>,
    left: <path d="m15 18-6-6 6-6"/>,
    right: <path d="m9 18 6-6-6-6"/>,
    download: <><path d="M12 3v12m0 0 4-4m-4 4-4-4"/><path d="M5 21h14"/></>,
    layers: <><path d="m12 2 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 17l9 5 9-5"/></>,
    file: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6"/></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

function TemplateCard({ template, onOpen }) {
  return (
    <button className="template-card" onClick={() => onOpen(template)} aria-label={`${template.title} 열기`}>
      <div className="card-preview">
        <img src={template.thumbnailUrl} alt="" loading="lazy" />
        <span className="open-chip">미리보기</span>
      </div>
      <div className="card-copy">
        <h3>{template.title}</h3>
        <div className="card-meta">
          <span>{template.category}</span>
          <i />
          <span>{formatBytes(template.size)}</span>
        </div>
      </div>
    </button>
  );
}

function ViewerModal({ template, onClose }) {
  const [slides, setSlides] = useState([]);
  const [current, setCurrent] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    setLoading(true);
    fetch(template.slidesUrl)
      .then((response) => response.ok ? response.json() : response.json().then((body) => Promise.reject(new Error(body.error))))
      .then((data) => { if (active) setSlides(data.slides || []); })
      .catch((reason) => { if (active) setError(reason.message || "ZIP을 열 수 없습니다."); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [template]);

  useEffect(() => {
    const onKey = (event) => {
      if (event.key === "Escape") onClose();
      if (event.key === "ArrowLeft") setCurrent((value) => Math.max(0, value - 1));
      if (event.key === "ArrowRight") setCurrent((value) => Math.min(Math.max(0, slides.length - 1), value + 1));
    };
    window.addEventListener("keydown", onKey);
    document.body.classList.add("modal-open");
    return () => { window.removeEventListener("keydown", onKey); document.body.classList.remove("modal-open"); };
  }, [onClose, slides.length]);

  const selected = slides[current];
  return (
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="viewer-modal" role="dialog" aria-modal="true" aria-label={`${template.title} 슬라이드 미리보기`}>
        <header className="modal-header">
          <div className="modal-title-wrap">
            <span className="modal-category">{template.category}</span>
            <h2>{template.title}</h2>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="닫기"><Icon name="close" size={22}/></button>
        </header>

        <div className="viewer-layout">
          <main className="stage-column">
            <div className="slide-stage">
              {loading && <div className="stage-message"><span className="spinner"/>ZIP 안의 시안을 여는 중...</div>}
              {error && <div className="stage-message error">{error}</div>}
              {!loading && !error && !selected && <div className="stage-message">ZIP에서 미리보기 이미지를 찾지 못했습니다.</div>}
              {selected && <img src={selected.url} alt={`${template.title} ${current + 1}번 슬라이드`} />}
              {slides.length > 1 && <>
                <button className="nav-arrow left" disabled={current === 0} onClick={() => setCurrent((value) => value - 1)} aria-label="이전 슬라이드"><Icon name="left" size={26}/></button>
                <button className="nav-arrow right" disabled={current === slides.length - 1} onClick={() => setCurrent((value) => value + 1)} aria-label="다음 슬라이드"><Icon name="right" size={26}/></button>
              </>}
            </div>
            <div className="stage-footer">
              <span>{slides.length ? `${current + 1} / ${slides.length}` : "미리보기 없음"}</span>
              <span className="keyboard-hint">← → 키로 이동</span>
            </div>
          </main>

          <aside className="details-column">
            <div className="detail-block">
              <span className="detail-label">파일</span>
              <strong>{template.filename}</strong>
              <span className="detail-sub">{formatBytes(template.size)}</span>
            </div>
            <a className="download-button" href={template.downloadUrl}>
              <Icon name="download" /> ZIP 다운로드
            </a>
            <div className="detail-divider" />
            <div className="thumb-heading"><span>템플릿 시안</span><b>{slides.length}</b></div>
            <div className="slide-thumbs">
              {slides.map((slide, index) => (
                <button key={slide.url} className={index === current ? "active" : ""} onClick={() => setCurrent(index)} aria-label={`${index + 1}번 슬라이드`}>
                  <img src={slide.url} alt="" loading="lazy" />
                  <span>{String(index + 1).padStart(2, "0")}</span>
                </button>
              ))}
            </div>
          </aside>
        </div>
      </section>
    </div>
  );
}

export default function App() {
  const [templates, setTemplates] = useState([]);
  const [category, setCategory] = useState(ALL);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/api/templates")
      .then((response) => response.ok ? response.json() : Promise.reject(new Error("목록을 불러오지 못했습니다.")))
      .then((data) => setTemplates(data.templates || []))
      .catch((reason) => setError(reason.message))
      .finally(() => setLoading(false));
  }, []);

  const categories = useMemo(() => [ALL, ...new Set(templates.map((item) => item.category))], [templates]);
  const filtered = useMemo(() => templates.filter((item) => {
    const matchesCategory = category === ALL || item.category === category;
    const term = query.trim().toLowerCase();
    return matchesCategory && (!term || `${item.title} ${item.filename}`.toLowerCase().includes(term));
  }), [templates, category, query]);

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="Slide Library 홈">
          <span className="brand-mark"><span/><span/><span/></span>
          <span>Slide Library</span>
        </a>
        <div className="topbar-actions">
          <label className="search-box">
            <Icon name="search" size={18}/>
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="템플릿 검색" aria-label="템플릿 검색" />
          </label>
          <span className="local-badge"><i/> Local</span>
        </div>
      </header>

      <main id="top">
        <section className="hero">
          <div className="eyebrow"><Icon name="layers" size={16}/> CURATED SLIDE ARCHIVE</div>
          <h1>아이디어를 깨우는<br/><em>슬라이드 템플릿</em></h1>
          <p>수집한 템플릿을 한곳에서 살펴보고, ZIP 안의 모든 시안을 바로 넘겨보세요.</p>
          <div className="hero-stats">
            <span><b>{templates.length}</b> templates</span>
            <i />
            <span><b>{Math.max(0, categories.length - 1)}</b> categories</span>
          </div>
        </section>

        <nav className="category-tabs" aria-label="템플릿 분류">
          {categories.map((item) => <button key={item} className={category === item ? "active" : ""} onClick={() => setCategory(item)}>{item}</button>)}
        </nav>

        <section className="gallery-section">
          <div className="section-heading">
            <div><span>{category === ALL ? "모든 템플릿" : category}</span><h2>{filtered.length}개의 슬라이드 컬렉션</h2></div>
            <span className="archive-note"><Icon name="file" size={16}/> ZIP 원본 기반</span>
          </div>

          {loading && <div className="empty-state"><span className="spinner"/>라이브러리를 정리하는 중...</div>}
          {error && <div className="empty-state error">{error}</div>}
          {!loading && !error && !filtered.length && <div className="empty-state"><Icon name="layers" size={34}/><strong>표시할 템플릿이 없습니다.</strong><span>다운로더를 실행하거나 검색어를 바꿔보세요.</span></div>}
          <div className="template-grid">
            {filtered.map((template) => <TemplateCard key={template.id} template={template} onOpen={setSelected}/>) }
          </div>
        </section>
      </main>

      <footer><span>Slide Library</span><span>Built for your local template archive.</span></footer>
      {selected && <ViewerModal template={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
