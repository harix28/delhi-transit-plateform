/* Ease between confirmed GPS samples only; never extrapolate bus locations. */
(function (root) {
  function createAnimator(options = {}) {
    const requestFrame = options.requestFrame || (callback => requestAnimationFrame(callback));
    const cancelFrame = options.cancelFrame || (id => cancelAnimationFrame(id));
    const now = options.now || (() => performance.now());
    const reducedMotion = options.reducedMotion || (() => window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    const duration = options.duration || 700;
    const motions = new Map();
    const lastTimestamp = new WeakMap();
    let frame = null;
    function tick(time) {
      frame = null;
      for (const [marker, motion] of motions) {
        const progress = Math.min(1, Math.max(0, (time - motion.start) / duration));
        const eased = progress * progress * (3 - 2 * progress);
        marker.setLatLng([motion.from.lat + (motion.to[0] - motion.from.lat) * eased,
                          motion.from.lng + (motion.to[1] - motion.from.lng) * eased]);
        if (progress === 1) motions.delete(marker);
      }
      if (motions.size) frame = requestFrame(tick);
    }
    function update(marker, lat, lon, timestamp) {
      if (![lat, lon, timestamp].every(Number.isFinite)) return false;
      const previous = lastTimestamp.get(marker);
      if (previous !== undefined && timestamp <= previous) return false;
      lastTimestamp.set(marker, timestamp);
      const from = marker.getLatLng();
      if (previous === undefined || reducedMotion() || Math.abs(lat - from.lat) + Math.abs(lon - from.lng) > 0.05) {
        motions.delete(marker);
        marker.setLatLng([lat, lon]);
      } else if (from.lat !== lat || from.lng !== lon) {
        motions.set(marker, {from: {lat: from.lat, lng: from.lng}, to: [lat, lon], start: now()});
        if (frame === null) frame = requestFrame(tick);
      }
      return true;
    }
    function remove(marker) {
      motions.delete(marker);
      lastTimestamp.delete(marker);
      if (!motions.size && frame !== null) { cancelFrame(frame); frame = null; }
    }
    return {update, remove};
  }
  root.TransitMotion = {createAnimator};
  if (typeof module !== 'undefined' && module.exports) module.exports = root.TransitMotion;
})(globalThis);
