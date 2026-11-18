// 팝업 신청 폼(버튼을 눌렀을 때만 외부 폼을 불러온다), 스크롤 등장, 하단 신청 바.
(function () {
  var d = document;
  d.documentElement.classList.add('js');

  // 본문 폼 높이 자동 조절: 네트워크 스크립트(iFrameResize)가 있으면 폼 내용 높이에 맞춘다
  window.addEventListener('load', function () {
    if (window.iFrameResize && d.querySelector('iframe.rform')) window.iFrameResize({ checkOrigin: false, heightCalculationMethod: 'lowestElement' }, 'iframe.rform');
  });

  // 팝업 폼: data-form-src 를 가진 버튼 → <dialog> 안 iframe 에 그때 주소를 넣는다
  var dlg = d.getElementById('formdlg');
  d.addEventListener('click', function (e) {
    var b = e.target.closest('[data-form-src]');
    if (b && dlg && dlg.showModal) {
      e.preventDefault();
      var f = dlg.querySelector('iframe');
      if (f.getAttribute('src') !== b.getAttribute('data-form-src')) f.setAttribute('src', b.getAttribute('data-form-src'));
      dlg.querySelector('[data-who]').textContent = b.getAttribute('data-who') || '';
      dlg.showModal();
    }
    if (e.target.closest('[data-close]') || e.target === dlg) dlg && dlg.close();
  });

  // 숫자 카운트(실제 데이터 값만 data-count 로 들어온다)
  var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function count(root) {
    if (still) return;
    root.querySelectorAll('[data-count]').forEach(function (el) {
      var to = parseInt(el.getAttribute('data-count'), 10), t0 = null;
      if (!(to > 1)) return;
      function step(t) {
        if (!t0) t0 = t;
        var k = Math.min((t - t0) / 900, 1);
        el.textContent = Math.round(to * (1 - Math.pow(1 - k, 3)));
        if (k < 1) requestAnimationFrame(step);
      }
      requestAnimationFrame(step);
    });
  }

  // 스크롤 등장
  var items = d.querySelectorAll('[data-reveal]');
  if ('IntersectionObserver' in window && items.length) {
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (x) { if (x.isIntersecting) { x.target.classList.add('in'); count(x.target); io.unobserve(x.target); } });
    }, { rootMargin: '0px 0px -8% 0px' });
    items.forEach(function (x) { io.observe(x); });
  } else {
    items.forEach(function (x) { x.classList.add('in'); });
  }

  // 하단 신청 바: 첫 화면을 지나면 보인다
  var bar = d.querySelector('.bar');
  if (bar) {
    d.body.classList.add('has-bar');
    var onScroll = function () { bar.classList.toggle('on', window.scrollY > 520); };
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
  }
})();
