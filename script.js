/* Стартовая страница «Выпрямись» — аккордеон вопросов, появление секций,
   проброс UTM-меток и цели Метрики. Без библиотек. */

/* ---------- 1. Вопросы ----------
   Открытый ответ схлопывается повторным кликом. Несколько одновременно
   открытых оставляем намеренно: человек часто сравнивает два ответа. */
(function () {
  document.querySelectorAll('.faq__q').forEach(function (btn) {
    var item = btn.closest('.faq__item');
    var panel = item.querySelector('.faq__a');

    btn.addEventListener('click', function () {
      var open = item.classList.toggle('is-open');
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
      // Высоту берём фактическую: так текст любой длины раскрывается целиком,
      // а после анимации снимаем ограничение, чтобы ответ не обрезался, если
      // строки перевёрстаются при смене ширины окна.
      panel.style.maxHeight = open ? panel.scrollHeight + 'px' : '0px';
    });

    panel.addEventListener('transitionend', function (e) {
      if (e.propertyName === 'max-height' && item.classList.contains('is-open')) {
        panel.style.maxHeight = 'none';
      }
    });

    // Перед закрытием возвращаем числовое значение — от `none` анимация
    // не пойдёт, ответ схлопнулся бы рывком.
    btn.addEventListener('mousedown', function () {
      if (item.classList.contains('is-open')) panel.style.maxHeight = panel.scrollHeight + 'px';
    });
  });
})();

/* ---------- 2. Появление секций при прокрутке ----------
   Через IntersectionObserver, а не на событие scroll: браузер сам считает
   пересечения, не дёргая главный поток на каждый пиксель. Если API нет —
   просто показываем всё сразу. */
(function () {
  var items = document.querySelectorAll('.reveal');
  if (!('IntersectionObserver' in window)) {
    items.forEach(function (el) { el.classList.add('is-in'); });
    return;
  }
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (e.isIntersecting) { e.target.classList.add('is-in'); io.unobserve(e.target); }
    });
  }, { rootMargin: '0px 0px -12% 0px', threshold: 0.05 });
  items.forEach(function (el) { io.observe(el); });
})();

/* ---------- 3. Проброс UTM-меток ----------
   Метки со ссылки страницы переносим на исходящие ссылки проекта, иначе
   переход «стартовая → интенсив» теряет источник и продажа записывается
   как прямой заход.

   Домены перечислены явно: club.dimafivex.ru (интенсивы и клуб) и
   walk-walk.ru (сайты Шагай Дома). max.ru и мессенджеры не трогаем —
   там метки бесполезны и только мусорят ссылку.

   Три слоя, как на других лендингах: при загрузке, на появление новых
   ссылок и в момент клика — виджеты умеют подставлять свои кнопки позже. */
(function () {
  var p = new URLSearchParams(window.location.search);
  var keys = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term',
              'utm_referrer', 'gclid', 'yclid', 'fbclid', 'erid', 'from'];
  var pairs = keys.map(function (k) {
    return p.has(k) ? k + '=' + encodeURIComponent(p.get(k)) : null;
  }).filter(Boolean);
  if (!pairs.length) return;
  var qs = pairs.join('&');
  try { sessionStorage.setItem('vypryamis_utm', qs); } catch (e) { /* приватный режим */ }

  var TARGET = /(^|\.)(dimafivex\.ru|walk-walk\.ru)$/i;

  function applyUtm(a) {
    if (!a || a.tagName !== 'A') return;
    var href = a.getAttribute('href');
    if (!href || href.charAt(0) === '#') return;
    var host;
    try { host = new URL(href, window.location.href).hostname; } catch (e) { return; }
    if (!TARGET.test(host)) return;
    if (/[?&]utm_/.test(href)) return;              // метки уже есть — не дублируем
    a.setAttribute('href', href + (href.indexOf('?') === -1 ? '?' : '&') + qs);
  }

  document.querySelectorAll('a[href]').forEach(applyUtm);

  if (window.MutationObserver) {
    new MutationObserver(function (muts) {
      muts.forEach(function (m) {
        m.addedNodes && m.addedNodes.forEach(function (node) {
          if (node.nodeType !== 1) return;
          if (node.tagName === 'A') applyUtm(node);
          if (node.querySelectorAll) node.querySelectorAll('a[href]').forEach(applyUtm);
        });
      });
    }).observe(document.body, { childList: true, subtree: true });
  }

  document.addEventListener('click', function (e) {
    var a = e.target && e.target.closest && e.target.closest('a[href]');
    if (a) applyUtm(a);
  }, true);
})();

/* ---------- 4. Цели Яндекс.Метрики ----------
   Счётчик здесь НЕ дублируется: он уже стоит на сайте, куда встраивается
   страница. Второй вызов ym(...) считал бы визиты дважды. Мы только шлём
   цели в существующий счётчик, если он есть. */
(function () {
  var METRIKA_ID = 94057307;
  document.addEventListener('click', function (e) {
    var t = e.target && e.target.closest && e.target.closest('[data-goal]');
    if (!t) return;
    var goal = t.getAttribute('data-goal');
    if (!goal) return;
    try {
      if (typeof window.ym === 'function') window.ym(METRIKA_ID, 'reachGoal', goal);
    } catch (err) { /* счётчика нет — молча пропускаем */ }
  });
})();
