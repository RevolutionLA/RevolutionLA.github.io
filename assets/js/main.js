/* ============================================================
   RevolutionLA · 深空实验室 SIGMA
   星辰粒子 · 滚动显现 · 导航高亮 · 年份
   ============================================================ */

(function () {
  'use strict';

  // 页脚年份
  var yearEl = document.getElementById('year');
  if (yearEl) yearEl.textContent = new Date().getFullYear();

  // ---------- 星辰粒子系统 ----------
  var canvas = document.getElementById('starfield');
  var ctx = canvas && canvas.getContext('2d');
  var stars = [];
  var w = 0, h = 0;
  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function resizeCanvas() {
    if (!canvas) return;
    // 位图按设备像素铺开、绘制坐标仍是 CSS 像素：否则 2x/3x 屏上每颗星
    // 都被 CSS 拉伸放大一圈，星点糊成一团。上限 2 是无畏的开销——3x 与 2x
    // 在这个密度下肉眼分不出，却多花一倍填充率。
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    // clientWidth 而不是 innerWidth：canvas 是 position:fixed，它铺满的是
    // 去掉滚动条后的视口（实测 375 vs 390），按 innerWidth 铺开会把星点横向挤掉 4%。
    w = document.documentElement.clientWidth || window.innerWidth;
    h = document.documentElement.clientHeight || window.innerHeight;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    initStars();
    // 改 canvas.width 会清屏。动画分支下一帧自然补回来，静态分支没人补——
    // 于是 reduced-motion 用户一旋转屏幕就只剩一片空黑。
    if (reduced) paintStatic();
  }

  function initStars() {
    stars = [];
    var density = Math.min(w, 1400) * 0.045; // 密度与宽度相关
    var count = Math.min(Math.round(density), 110);
    for (var i = 0; i < count; i++) {
      stars.push({
        x: Math.random() * w,
        y: Math.random() * h,
        r: Math.random() * 1.4 + 0.3,
        baseOpacity: Math.random() * 0.5 + 0.15,
        twinkle: Math.random() * Math.PI * 2,
        speed: 0.0015 + Math.random() * 0.0035
      });
    }
  }

  var rafId = null;
  function paintStatic() {
    ctx.clearRect(0, 0, w, h);
    for (var i = 0; i < stars.length; i++) {
      ctx.beginPath();
      ctx.arc(stars[i].x, stars[i].y, stars[i].r, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(223,230,238,' + stars[i].baseOpacity.toFixed(3) + ')';
      ctx.fill();
    }
  }

  function draw() {
    ctx.clearRect(0, 0, w, h);
    for (var i = 0; i < stars.length; i++) {
      var s = stars[i];
      var alpha = s.baseOpacity * (reduced ? 1 : (0.5 + 0.5 * Math.sin(s.twinkle)));
      ctx.beginPath();
      ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(223, 230, 238, ' + alpha.toFixed(3) + ')';
      ctx.fill();
      if (!reduced) s.twinkle += s.speed;
    }
    rafId = requestAnimationFrame(draw);
  }

  if (canvas && ctx) {
    resizeCanvas();
    if (!reduced) {
      draw();
    } else {
      // reduced motion: 只静态渲染一帧
      paintStatic();
    }
    var t;
    window.addEventListener('resize', function () {
      clearTimeout(t);
      t = setTimeout(resizeCanvas, 150);
    });
  }

  // ---------- 滚动显现 ----------
  var revealEls = document.querySelectorAll(
    '.sec-head, .about-grid, .stack-card, .tl-item, .sig-card, .contact-inner'
  );
  if ('IntersectionObserver' in window && !reduced) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) {
          en.target.classList.add('visible');
          io.unobserve(en.target);
        }
      });
    }, { threshold: 0.1, rootMargin: '0px 0px -8% 0px' });
    revealEls.forEach(function (el) { el.classList.add('reveal'); io.observe(el); });
  } else {
    revealEls.forEach(function (el) {
      el.classList.add('visible');
    });
  }

  // ---------- 导航点击平滑滚动 + 高亮 ----------
  var navAnchors = document.querySelectorAll('.nav-links a');
  var sectionMap = {};
  navAnchors.forEach(function (a) {
    var id = a.getAttribute('href');
    var sec = id && document.querySelector(id);
    if (sec) sectionMap[id] = sec;
  });

  function updateActive() {
    var pos = window.scrollY + 120;
    var currentId = null;
    Object.keys(sectionMap).forEach(function (id) {
      var sec = sectionMap[id];
      if (sec.offsetTop <= pos) currentId = id;
    });
    navAnchors.forEach(function (a) {
      var active = currentId && a.getAttribute('href') === currentId;
      a.style.color = active ? 'var(--signal)' : '';
    });
  }

  if (Object.keys(sectionMap).length) {
    var scrollTimer;
    window.addEventListener('scroll', function () {
      clearTimeout(scrollTimer);
      scrollTimer = setTimeout(updateActive, 60);
    }, { passive: true });
    updateActive();
  }

  // ---------- 窄屏抽屉导航 ----------
  var header = document.querySelector('.site-header');
  var toggle = document.querySelector('.nav-toggle');
  var toggleText = toggle && toggle.querySelector('.nt-text');

  function setNavOpen(open) {
    header.classList.toggle('nav-open', open);
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    toggle.setAttribute('aria-label', (open ? '关闭' : '打开') + '导航菜单');
    if (toggleText) toggleText.textContent = open ? 'CLOSE' : 'MENU';
  }

  if (toggle && header) {
    toggle.addEventListener('click', function () {
      setNavOpen(!header.classList.contains('nav-open'));
    });
    // 选完就收起：抽屉盖在正文上面，不收起的话点一次就等于挡住一次
    navAnchors.forEach(function (a) {
      a.addEventListener('click', function () { setNavOpen(false); });
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && header.classList.contains('nav-open')) {
        setNavOpen(false);
        toggle.focus();
      }
    });
    window.addEventListener('resize', function () {
      if (window.innerWidth > 760) setNavOpen(false);
    });
  }
})();
