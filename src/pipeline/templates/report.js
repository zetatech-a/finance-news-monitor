
(function(){
  const NO_TOPIC_TOKEN = "__NO_TOPIC__";
  const root = document.documentElement;
  const themeBtn = document.getElementById("themeBtn");
  const search = document.getElementById("searchInput");
  const topOnly = document.getElementById("topOnly");
  const favOnly = document.getElementById("favOnly");
  const sortSel = document.getElementById("sortSel");
  const emptyState = document.getElementById("emptyState");
  const resultCount = document.getElementById("resultCount");
  const savedCount = document.getElementById("savedCount");
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

  // 결과 개수 표시는 카드가 아니라 기사 단위 — Top 섹션 사본이 두 번 세지 않게 한다.
  function cardKey(meta){ return meta.url || ("card-" + meta.id); }
  const totalArticleCount = new Set(cardMetas.map(cardKey)).size;
  // 이 리포트에 실제로 있는 기사 URL. 즐겨찾기는 날짜별 리포트가 같은 origin에서
  // localStorage를 공유하므로, 저장 건수는 반드시 이 집합과 교집합으로 센다 —
  // 어제 저장한 기사까지 세면 '저장 1건'인데 '저장만'은 0건인 상태가 된다.
  const articleUrls = new Set(cardMetas.map(meta => meta.url).filter(Boolean));

  let renderRafId = 0;
  let pendingRender = { recomputeMatch: true, recomputeSort: true, resetPagination: true };

  function applyThemeLabel(theme){
    if(!themeBtn) return;
    const dark = theme === "dark";
    themeBtn.textContent = dark ? "라이트" : "다크";
    // 상태를 색상이 아니라 라벨과 aria-pressed로도 알린다.
    themeBtn.setAttribute("aria-pressed", dark ? "true" : "false");
    themeBtn.setAttribute("aria-label", dark ? "라이트 모드로 전환" : "다크 모드로 전환");
  }
  function loadTheme(){ const saved = localStorage.getItem(LS_THEME); root.dataset.theme = (saved === "dark" || saved === "light") ? saved : "light"; applyThemeLabel(root.dataset.theme); }
  function toggleTheme(){ const next = (root.dataset.theme === "dark") ? "light" : "dark"; root.dataset.theme = next; localStorage.setItem(LS_THEME, next); applyThemeLabel(next); }
  function getFavs(){ try{ const raw = localStorage.getItem(LS_FAVS); const arr = raw ? JSON.parse(raw) : []; return new Set(Array.isArray(arr) ? arr : []);}catch(e){ return new Set(); }}
  function saveFavs(set){ localStorage.setItem(LS_FAVS, JSON.stringify(Array.from(set))); }
  function updateSavedCount(favs){
    if(!savedCount) return;
    let inReport = 0;
    favs.forEach(url => { if(articleUrls.has(url)) inReport += 1; });
    savedCount.textContent = "저장 " + inReport + "건";
  }
  function setActivePill(sector){
    const resolvedSector = pills.some(p => p.dataset.sector === sector) ? sector : "ALL";
    activeSector = resolvedSector;
    pills.forEach(p => {
      const on = p.dataset.sector === resolvedSector;
      p.classList.toggle("active", on);
      p.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }
  function setActiveTopicPill(topic){
    const resolvedTopic = topicPills.some(p => p.dataset.topic === topic) ? topic : "ALL";
    activeTopic = resolvedTopic;
    topicPills.forEach(p => {
      const on = p.dataset.topic === resolvedTopic;
      p.classList.toggle("active", on);
      p.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }
  function debounce(fn, waitMs){ let timer = 0; return (...args) => { clearTimeout(timer); timer = window.setTimeout(() => fn(...args), waitMs); }; }

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
    if (resultCount){
      // 같은 기사가 Top 섹션과 업권 섹션에 각각 렌더되므로 기사 단위로 센다.
      const matched = new Set();
      cardMetas.forEach(meta => { if(meta.isMatch) matched.add(cardKey(meta)); });
      const label = (matched.size === totalArticleCount) ? "전체 " : "검색 결과 ";
      resultCount.textContent = label + matched.size + "건";
    }
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

  function paintFavButton(btn, on){
    btn.classList.toggle("on", on);
    btn.textContent = on ? "★" : "☆";
    // 저장 여부를 아이콘 모양과 aria-pressed로 함께 알린다(색상만으로 표현하지 않는다).
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.setAttribute("aria-label", on ? "저장 해제" : "이 기사 저장");
    btn.setAttribute("title", on ? "저장 해제" : "저장");
  }

  function initFavButtons(){
    const favs = getFavs();
    // 같은 기사가 Top/업권 섹션에 두 번 렌더되므로 URL이 같은 버튼은 함께 갱신한다.
    const buttonsByUrl = new Map();
    cards.forEach(card => {
      const btn = card.querySelector("[data-clip]"); const url = card.dataset.url || ""; if(!btn || !url) return;
      if(!buttonsByUrl.has(url)) buttonsByUrl.set(url, []);
      buttonsByUrl.get(url).push(btn);
      paintFavButton(btn, favs.has(url));
      btn.addEventListener("click", () => {
        const set = getFavs();
        const nowOn = set.has(url) ? (set.delete(url), false) : (set.add(url), true);
        saveFavs(set);
        (buttonsByUrl.get(url) || [btn]).forEach(other => paintFavButton(other, nowOn));
        updateSavedCount(set);
        if(favOnly && favOnly.checked) applyFilter();
      });
    });
    updateSavedCount(getFavs());
  }

  function bindEvents(){
    pills.forEach(p => p.addEventListener("click", () => { setActivePill(p.dataset.sector); applyFilter(); }));
    topicPills.forEach(p => p.addEventListener("click", () => { setActiveTopicPill(p.dataset.topic); applyFilter(); }));
    groupMetas.forEach(g => { const btn = g.loadMoreBtn; if(!btn) return; btn.addEventListener("click", ()=>{ g.visibleLimit += PAGE_SIZE; scheduleRender({ resetPagination: false }); }); });
    const debouncedSearch = debounce(()=>{ applyFilter(); }, SEARCH_DEBOUNCE_MS);
    search?.addEventListener("input", debouncedSearch);
    if(topOnly) topOnly.addEventListener("change", applyFilter);
    if(favOnly) favOnly.addEventListener("change", applyFilter);
    sortSel?.addEventListener("change", ()=>{ applySort(); applyFilter(); });
    themeBtn?.addEventListener("click", toggleTheme);

    navElements.forEach(nav => nav.addEventListener("scroll", updateNavScrollHints, { passive: true }));
    window.addEventListener("resize", updateNavScrollHints);
  }

  loadTheme();
  setActivePill("ALL");
  setActiveTopicPill("ALL");
  applySort();
  initFavButtons();
  bindEvents();
  applyFilter();
  updateNavScrollHints();
})();
