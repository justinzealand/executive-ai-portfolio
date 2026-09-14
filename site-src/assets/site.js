const toggle = document.querySelector('.nav-toggle');
const navigation = document.querySelector('.primary-nav');
const header = document.querySelector('.site-header');
const wordmark = document.querySelector('.wordmark');

if (toggle && navigation && header && wordmark) {
  let updateQueued = false;

  const updateNavigationMode = () => {
    updateQueued = false;
    header.classList.remove('is-cramped');
    navigation.classList.add('is-measuring');
    const requiredWidth = wordmark.getBoundingClientRect().width + navigation.getBoundingClientRect().width + 32;
    navigation.classList.remove('is-measuring');

    const compact = window.innerWidth <= 1180 || requiredWidth > header.clientWidth;
    header.classList.toggle('is-compact', compact);
    const cramped = compact && wordmark.getBoundingClientRect().width + toggle.getBoundingClientRect().width + 24 > header.clientWidth;
    header.classList.toggle('is-cramped', cramped);
    if (!compact) {
      toggle.setAttribute('aria-expanded', 'false');
      navigation.classList.remove('is-open');
    }
  };

  const queueNavigationUpdate = () => {
    if (updateQueued) return;
    updateQueued = true;
    window.requestAnimationFrame(updateNavigationMode);
  };

  toggle.addEventListener('click', () => {
    const open = toggle.getAttribute('aria-expanded') === 'true';
    toggle.setAttribute('aria-expanded', String(!open));
    navigation.classList.toggle('is-open', !open);
  });

  window.addEventListener('resize', queueNavigationUpdate);
  new ResizeObserver(queueNavigationUpdate).observe(header);
  new MutationObserver(queueNavigationUpdate).observe(header, {
    attributes: true,
    attributeFilter: ['style'],
    subtree: true,
  });
  queueNavigationUpdate();
}
