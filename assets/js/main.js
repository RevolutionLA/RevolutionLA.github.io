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
    w = canvas.width = window.innerWidth;
    h = canvas.height = window.innerHeight;
    initStars();
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
    initStars();
    if (!reduced) {
      draw();
    } else {
      // reduced motion: 只静态渲染一帧
      ctx.clearRect(0, 0, w, h);
      for (var i = 0; i < stars.length; i++) {
        ctx.beginPath();
        ctx.arc(stars[i].x, stars[i].y, stars[i].r, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(223,230,238,' + stars[i].baseOpacity.toFixed(3) + ')';
        ctx.fill();
      }
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
})();
