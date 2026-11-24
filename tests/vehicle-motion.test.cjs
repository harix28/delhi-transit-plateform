const {test} = require('node:test');
const assert = require('node:assert/strict');
const {createAnimator} = require('../frontend/vehicle-motion.js');
function setup(reduced = false) {
  let time = 0, callback = null;
  const marker = {position: {lat: 28, lng: 77}, getLatLng() {return this.position;}, setLatLng(p) {this.position = {lat:p[0],lng:p[1]};}};
  const animator = createAnimator({now: () => time, requestFrame: cb => {callback = cb; return 1;}, cancelFrame: () => {callback = null;}, reducedMotion: () => reduced});
  return {marker, animator, advance(t) {time=t; const cb=callback; callback=null; if(cb)cb(t);}};
}
test('animates between confirmed samples and stops at the reported endpoint', () => {
  const {marker, animator, advance} = setup();
  animator.update(marker,28,77,100);
  animator.update(marker,28.01,77.01,101);
  advance(350);
  assert.ok(Math.abs(marker.position.lat - 28.005) < 1e-9);
  advance(700);
  assert.deepEqual(marker.position,{lat:28.01,lng:77.01});
  advance(2000);
  assert.deepEqual(marker.position,{lat:28.01,lng:77.01});
});
test('ignores duplicate and older GPS timestamps', () => {
  const {marker,animator}=setup();
  animator.update(marker,28,77,100);
  assert.equal(animator.update(marker,28.01,77.01,100),false);
  assert.equal(animator.update(marker,28.01,77.01,99),false);
  assert.deepEqual(marker.position,{lat:28,lng:77});
});
test('removed markers stop animating', () => {
  const {marker,animator,advance}=setup();
  animator.update(marker,28,77,100);
  animator.update(marker,28.01,77.01,101);
  animator.remove(marker);
  advance(700);
  assert.deepEqual(marker.position,{lat:28,lng:77});
});
test('reduced motion shows the confirmed point immediately', () => {
  const {marker,animator}=setup(true);
  animator.update(marker,28,77,100);
  animator.update(marker,28.01,77.01,101);
  assert.deepEqual(marker.position,{lat:28.01,lng:77.01});
});
