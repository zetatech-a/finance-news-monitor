
(function(){
  const NO_TOPIC_TOKEN = "__NO_TOPIC__";
  const root = document.documentElement;
  const body = document.body;
  const themeBtn = document.getElementById("themeBtn");
  const search = document.getElementById("searchInput");
  const topOnly = document.getElementById("topOnly");
  const favOnly = document.getElementById("favOnly");
  const sortSel = document.getElementById("sortSel");
  const emptyState = document.getElementById("emptyState");
  const sidebar = document.getElementById("filterSidebar");
  const mobileFilterBtn = document.getElementById("mobileFilterBtn");
  const mobileSearchBtn = document.getElementById("mobileSearchBtn");
  const mobileTopBtn = document.getElementById("mobileTopBtn");
  const navElements = Array.from(document.querySelectorAll(".nav-wrap[data-scroll-hint] .nav"));

  const pills = Array.from(document.querySelectorAll("[data-sector-pill]"));
  const topicPills = Array.from(document.querySelectorAll("[data-topic-pill]"));
  const cards = Array.from(document.querySelectorAll("[data-card]"));
  const groups = Array.from(document.querySelectorAll("[data-group]"));
  const PAGE_SIZE = 20;
  const SEARCH_DEBOUNCE_MS = 150;

  const LS_THEME = "reportTheme";
  const LS_FAVS = "reportFavs_v1";
  let activeSector = "ALL";
  let activeTopic = "ALL";

  const groupMetaMap = new Map();
  const groupMetas = groups.map(groupEl => {
    const meta = {
      el: groupEl,
      grid: groupEl.querySelector(".grid"),
      loadMoreBtn: groupEl.querySelector("[data-load-more]"),
      cards: [],
      orderedCards: [],
      visibleLimit: PAGE_SIZE,
      lastOrder: []
    };
    groupMetaMap.set(groupEl, meta);
    return meta;
  });

  const cardMetas = cards.map((cardEl, idx) => {
    const groupMeta = groupMetaMap.get(cardEl.closest("[data-group]"));
    const meta = {
      id: idx,
      el: cardEl,
      group: groupMeta,
      hay: (cardEl.dataset.hay || "").toLowerCase(),
      sector: cardEl.dataset.sector || "",
      topics: (() => {
        const values = (cardEl.dataset.topics || "").split("|").filter(Boolean);
        if(values.length === 0) values.push(NO_TOPIC_TOKEN);
        return values;
      })(),
      ts: parseFloat(cardEl.dataset.ts || "0"),
      rel: parseFloat(cardEl.dataset.rel || "0"),
      isTop: cardEl.dataset.top === "1",
      url: cardEl.dataset.url || "",
      isMatch: cardEl.dataset.match === "1",
      isShown: cardEl.style.display !== "none"
    };
    if(groupMeta) groupMeta.cards.push(meta);
    return meta;
  });
  groupMetas.forEach(g => { g.orderedCards = g.cards.slice(); g.lastOrder = g.cards.slice(); });

  let renderRafId = 0;
  let pendingRender = { recomputeMatch: true, recomputeSort: true, resetPagination: true };

  function loadTheme(){ const saved = localStorage.getItem(LS_THEME); root.dataset.theme = (saved === "dark" || saved === "light") ? saved : "light"; if(themeBtn) themeBtn.textContent = (root.dataset.theme === "dark") ? "라이트" : "다크"; }
  function toggleTheme(){ const next = (root.dataset.theme === "dark") ? "light" : "dark"; root.dataset.theme = next; localStorage.setItem(LS_THEME, next); if(themeBtn) themeBtn.textContent = (next === "dark") ? "라이트" : "다크"; }
  function getFavs(){ try{ const raw = localStorage.getItem(LS_FAVS); const arr = raw ? JSON.parse(raw) : []; return new Set(Array.isArray(arr) ? arr : []);}catch(e){ return new Set(); }}
  function saveFavs(set){ localStorage.setItem(LS_FAVS, JSON.stringify(Array.from(set))); }
  function setActivePill(sector){
    const resolvedSector = pills.some(p => p.dataset.sector === sector) ? sector : "ALL";
    activeSector = resolvedSector;
    pills.forEach(p => p.classList.toggle("active", p.dataset.sector === resolvedSector));
  }
  function setActiveTopicPill(topic){
    const resolvedTopic = topicPills.some(p => p.dataset.topic === topic) ? topic : "ALL";
    activeTopic = resolvedTopic;
    topicPills.forEach(p => p.classList.toggle("active", p.dataset.topic === resolvedTopic));
  }
  function debounce(fn, waitMs){ let timer = 0; return (...args) => { clearTimeout(timer); timer = window.setTimeout(() => fn(...args), waitMs); }; }
  function setFilterSheetOpen(nextOpen, options){
    const focusSearch = !!options?.focusSearch;
    const isMobile = window.innerWidth < 768;
    const open = !!nextOpen && isMobile;
    if(sidebar){
      sidebar.classList.toggle("open", open);
      sidebar.setAttribute("aria-hidden", open ? "false" : "true");
    }
    body.classList.toggle("no-scroll", open);
    if(mobileFilterBtn){
      mobileFilterBtn.setAttribute("aria-expanded", open ? "true" : "false");
      mobileFilterBtn.setAttribute("aria-label", open ? "필터 닫기" : "필터 열기");
      mobileFilterBtn.textContent = "필터";
    }
    if(open && focusSearch) setTimeout(() => search?.focus(), 120);
  }

  function updateNavScrollHints(){
    navElements.forEach(nav => {
      const wrap = nav.closest(".nav-wrap");
      if(!wrap) return;
      const maxLeft = Math.max(0, nav.scrollWidth - nav.clientWidth);
      const canScroll = maxLeft > 2;
      wrap.classList.toggle("can-scroll-left", canScroll && nav.scrollLeft > 2);
      wrap.classList.toggle("can-scroll-right", canScroll && nav.scrollLeft < maxLeft - 2);
    });
  }

  function sortCards(mode, cardsToSort){
    return cardsToSort.slice().sort((a,b) => {
      if(mode === "rel"){
        if(b.rel !== a.rel) return b.rel - a.rel;
        return b.ts - a.ts;
      }
      return b.ts - a.ts;
    });
  }

  function computeFilterState(){
    const q = (search?.value || "").trim().toLowerCase();
    const qTokens = q ? q.split(/\s+/).filter(Boolean) : [];
    const onlyTop = !!(topOnly && topOnly.checked);
    const onlyFav = !!(favOnly && favOnly.checked);
    const favs = getFavs();
    let totalMatched = 0;

    cardMetas.forEach(meta => {
      const isFav = favs.has(meta.url);
      let ok = true;
      if(activeSector !== "ALL" && meta.sector !== activeSector) ok = false;
      if(activeTopic !== "ALL" && !meta.topics.includes(activeTopic)) ok = false;
      if(onlyTop && !meta.isTop) ok = false;
      if(onlyFav && !isFav) ok = false;
      if(qTokens.length && !qTokens.some(tok => meta.hay.includes(tok))) ok = false;
      meta.isMatch = ok;
      if(ok) totalMatched += 1;
    });
    return totalMatched;
  }

  function applyDomState(totalMatched){
    groupMetas.forEach(g => {
      const matchedOrdered = g.orderedCards.filter(meta => meta.isMatch);
      const matchedCount = matchedOrdered.length;
      if(g.visibleLimit < PAGE_SIZE) g.visibleLimit = PAGE_SIZE;
      const visibleLimit = Math.min(g.visibleLimit, matchedCount);

      let shownMatched = 0;
      g.orderedCards.forEach(meta => {
        const shouldShow = meta.isMatch && shownMatched < visibleLimit;
        if(meta.isMatch) shownMatched += 1;
        if((meta.el.dataset.match === "1") !== meta.isMatch) meta.el.dataset.match = meta.isMatch ? "1" : "0";
        if(meta.isShown !== shouldShow){
          meta.el.style.display = shouldShow ? "" : "none";
          meta.isShown = shouldShow;
        }
      });

      if(g.loadMoreBtn){
        const nextOffset = Math.min(visibleLimit, matchedCount);
        if(g.loadMoreBtn.dataset.offset !== String(nextOffset)) g.loadMoreBtn.dataset.offset = String(nextOffset);
        const showLoadMore = visibleLimit < matchedCount;
        if((g.loadMoreBtn.style.display !== "none") !== showLoadMore) g.loadMoreBtn.style.display = showLoadMore ? "" : "none";
      }
      const showGroup = matchedCount > 0;
      if((g.el.style.display !== "none") !== showGroup) g.el.style.display = showGroup ? "" : "none";
    });
    if (emptyState) emptyState.style.display = totalMatched > 0 ? "none" : "";
  }

  function runRender(){
    renderRafId = 0;
    const task = pendingRender;
    pendingRender = { recomputeMatch: false, recomputeSort: false, resetPagination: false };
    const mode = (sortSel?.value || "new");

    groupMetas.forEach(g => {
      if(task.recomputeSort){
        const nextOrder = sortCards(mode, g.cards);
        const sameOrder = nextOrder.length === g.lastOrder.length && nextOrder.every((meta, idx) => meta === g.lastOrder[idx]);
        g.orderedCards = nextOrder;
        if(!sameOrder && g.grid){
          nextOrder.forEach(meta => g.grid.appendChild(meta.el));
          g.lastOrder = nextOrder.slice();
        }
      }
      if(task.resetPagination) g.visibleLimit = PAGE_SIZE;
    });

    let totalMatched = cardMetas.reduce((acc, meta) => acc + (meta.isMatch ? 1 : 0), 0);
    if(task.recomputeMatch) totalMatched = computeFilterState();
    applyDomState(totalMatched);
  }

  function scheduleRender(nextTask){
    pendingRender = {
      recomputeMatch: pendingRender.recomputeMatch || !!nextTask?.recomputeMatch,
      recomputeSort: pendingRender.recomputeSort || !!nextTask?.recomputeSort,
      resetPagination: pendingRender.resetPagination || !!nextTask?.resetPagination,
    };
    if(renderRafId) return;
    renderRafId = window.requestAnimationFrame(runRender);
  }

  function applySort(){
    scheduleRender({ recomputeSort: true, resetPagination: true });
  }

  function applyFilter(){
    scheduleRender({ recomputeMatch: true, resetPagination: true });
  }

  function initFavButtons(){
    const favs = getFavs();
    cards.forEach(card => {
      const btn = card.querySelector("[data-clip]"); const url = card.dataset.url || ""; if(!btn || !url) return;
      const on = favs.has(url); btn.classList.toggle("on", on); btn.textContent = on ? "★" : "☆";
      btn.addEventListener("click", () => { const set = getFavs(); const nowOn = set.has(url) ? (set.delete(url), false) : (set.add(url), true); saveFavs(set); btn.classList.toggle("on", nowOn); btn.textContent = nowOn ? "★" : "☆"; if(favOnly && favOnly.checked) applyFilter(); });
    });
  }

  function bindEvents(){
    pills.forEach(p => p.addEventListener("click", () => { setActivePill(p.dataset.sector); applyFilter(); if(window.innerWidth < 768) setFilterSheetOpen(false); }));
    topicPills.forEach(p => p.addEventListener("click", () => { setActiveTopicPill(p.dataset.topic); applyFilter(); if(window.innerWidth < 768) setFilterSheetOpen(false); }));
    groupMetas.forEach(g => { const btn = g.loadMoreBtn; if(!btn) return; btn.addEventListener("click", ()=>{ g.visibleLimit += PAGE_SIZE; scheduleRender({ resetPagination: false }); }); });
    const debouncedSearch = debounce(()=>{ applyFilter(); }, SEARCH_DEBOUNCE_MS);
    search?.addEventListener("input", debouncedSearch);
    if(topOnly) topOnly.addEventListener("change", applyFilter);
    if(favOnly) favOnly.addEventListener("change", applyFilter);
    sortSel?.addEventListener("change", ()=>{ applySort(); applyFilter(); });
    themeBtn?.addEventListener("click", toggleTheme);

    mobileFilterBtn?.addEventListener("click", () => setFilterSheetOpen(!sidebar?.classList.contains("open")));
    mobileSearchBtn?.addEventListener("click", () => setFilterSheetOpen(true, { focusSearch: true }));
    sidebar?.addEventListener("click", (e)=>{ if(e.target.closest("[data-sheet-close]")) setFilterSheetOpen(false); });
    document.addEventListener("keydown", (e)=>{ if(e.key === "Escape") setFilterSheetOpen(false); });
    mobileTopBtn?.addEventListener("click", ()=>{ if(!topOnly) return; topOnly.checked = !topOnly.checked; mobileTopBtn.classList.toggle("active", topOnly.checked); applyFilter(); });
    topOnly?.addEventListener("change", ()=> mobileTopBtn?.classList.toggle("active", topOnly.checked));
    navElements.forEach(nav => nav.addEventListener("scroll", updateNavScrollHints, { passive: true }));
    window.addEventListener("resize", ()=>{ setFilterSheetOpen(false); updateNavScrollHints(); });
  }

  loadTheme();
  setActivePill("ALL");
  setActiveTopicPill("ALL");
  applySort();
  initFavButtons();
  bindEvents();
  applyFilter();
  setFilterSheetOpen(false);
  updateNavScrollHints();
  mobileTopBtn?.classList.toggle("active", !!topOnly?.checked);
})();
