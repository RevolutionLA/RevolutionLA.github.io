/* ============================================================
   RevolutionLA · 刘昂 — 极简科技风 交互脚本
   ============================================================ */

(function () {
  'use strict';

  // 页脚年份自动更新
  var yearEl = document.getElementById('year');
  if (yearEl) yearEl.textContent = new Date().getFullYear();

  // 为所有主要区块统一添加 reveal，并统一通过观察者触发显现
  var targets = document.querySelectorAll(
    '.section-head, .about-body, .stack-card, .timeline-item, .phil-card, .contact-note, .contact-actions'
  );

  function initReveal() {
    if ('IntersectionObserver' in window) {
      var observer = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (entry) {
            if (entry.isIntersecting) {
              entry.target.classList.add('visible');
              observer.unobserve(entry.target);
            }
          });
        },
        { threshold: 0.12 }
      );
      targets.forEach(function (el) {
        el.classList.add('reveal');
        observer.observe(el);
      });
    } else {
      targets.forEach(function (el) { el.classList.add('visible'); });
    }
  }

  // script 位于 body 末尾，DOM 已就绪；但仍以 rAF 确保样式计算无误
  if (document.readyState !== 'loading') {
    requestAnimationFrame(initReveal);
  } else {
    document.addEventListener('DOMContentLoaded', initReveal);
  }
})();
