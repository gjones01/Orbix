/** Decorative motion only: never touches positions, camera, picking or Python state. */
export function createMotion() {
  const preference = matchMedia('(prefers-reduced-motion: reduce)');
  const animations = new Set();
  let visible = true;
  const active = () => visible && !document.hidden && !preference.matches;

  function sync() {
    document.documentElement.dataset.motion = active() ? 'active' : 'quiet';
    if (!active()) {
      animations.forEach(a => a.cancel()); animations.clear();
    }
  }
  document.addEventListener('visibilitychange', sync);
  preference.addEventListener('change', sync);
  // IntersectionObserver also detects a Streamlit tab hiding this iframe.
  const observer = new IntersectionObserver(entries => {
    visible = entries[0].isIntersecting; sync();
  });
  observer.observe(document.documentElement);
  sync();
  return {
    get reduced() { return preference.matches; },
    get active() { return active(); },
    emphasize(element) {
      if (!active() || !element) return;
      element.getAnimations().forEach(a => a.cancel());
      const animation = element.animate([
        { opacity: .78, transform: 'translateY(3px)' },
        { opacity: 1, transform: 'translateY(0)' },
      ], { duration: 240, easing: 'cubic-bezier(.2,.7,.2,1)' });
      animations.add(animation);
      animation.finished.catch(() => {}).finally(() => animations.delete(animation));
    },
  };
}
