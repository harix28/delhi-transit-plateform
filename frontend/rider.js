const $ = id => document.getElementById(id);
const safeSetText = (id, text) => { const el = $(id); if (el) el.textContent = text; };
const element = (tag, text) => { const e = document.createElement(tag); e.textContent = text; return e; };
async function get(url) { const r = await fetch(url); const body = await r.json(); if (!r.ok) throw Error(typeof body.detail === 'string' ? body.detail : 'Data temporarily unavailable'); return body; }
document.querySelector('.app').classList.add('map-mode');
safeSetText('dataset-pill', 'Live GPS'); safeSetText('data-notice', 'Data: Delhi Transport Stack · Map: OpenStreetMap');
safeSetText('map-headline', 'Delhi buses, live'); safeSetText('map-description', 'Loading latest positions…');
if ($('planner-nav')) $('planner-nav').hidden = true; if ($('network-nav')) $('network-nav').hidden = true;
if ($('map-nav')) { $('map-nav').classList.add('active'); $('map-nav').onclick = () => map.invalidateSize(); }
const box = document.createElement('div'); box.id = 'street-map'; $('map').replaceWith(box);
const map = L.map(box, { zoomControl: false, preferCanvas: true }).setView([28.6315, 77.2167], 13);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap contributors' }).addTo(map);
const buses = L.layerGroup().addTo(map), stands = L.layerGroup().addTo(map), paths = L.layerGroup().addTo(map), hotspots = L.layerGroup().addTo(map);
let vehicles = [], filter = null, sequence = 0; let activeBusType = 'all'; let hotspotsActive = false;

function haversineM(lat1, lon1, lat2, lon2) {
  const R = 6371e3;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
    Math.sin(dLon / 2) * Math.sin(dLon / 2);
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

// Where Is My Bus: Dedicated Live Route Station Tracker
let currentTrackedVehicle = null;
let currentTrackedRouteId = null;
let currentTrackedRouteName = null;
let currentTrackedSegments = null;
let showRouteOnMapGlobal = null;
const routeStopsCache = new Map();

async function openBusTracker(vehicle, routeId, routeName, segments, isBackgroundUpdate = false) {
  currentTrackedVehicle = vehicle;
  currentTrackedRouteId = routeId;
  currentTrackedRouteName = routeName;
  if (segments !== undefined) currentTrackedSegments = segments;

  const sheet = $('bus-tracker-sheet');
  if (!sheet) return;

  const routeCode = routeName || (vehicle && vehicle.route_id) || routeId || 'BUS';
  const queryRoute = routeId || routeCode;
  const busId = (vehicle && vehicle.id) ? ('Bus ' + vehicle.id) : ('Route ' + routeCode);
  const isAC = vehicle && vehicle.bus_type === 'ac';

  const listEl = $('stations-timeline-list');
  const existingStationNodes = listEl ? listEl.querySelectorAll('.station-node') : [];
  const isAlreadyOpen = !sheet.hidden &&
    (sheet.dataset.trackedRoute === String(queryRoute)) &&
    existingStationNodes.length > 0;

  if (!isAlreadyOpen) {
    sheet.hidden = false;
    sheet.scrollTop = 0;
    sheet.dataset.trackedRoute = String(queryRoute);
  }

  safeSetText('tracker-route-num', routeCode);
  const acBadge = $('tracker-ac-badge');
  if (acBadge) acBadge.style.display = isAC ? 'inline-block' : 'none';

  const regEl = $('tracker-bus-reg');
  if (regEl) {
    if (vehicle && vehicle.timestamp) {
      const timeStr = new Date(vehicle.timestamp * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      regEl.textContent = `${busId} · Updated ${timeStr}`;
    } else {
      regEl.textContent = `${busId} · Live Station Tracker`;
    }
  }

  const currentStationEl = $('tracker-current-station');
  const nextEtaEl = $('tracker-next-eta');

  // ONLY show loading state if the sheet is NOT already open and showing this route
  if (!isAlreadyOpen) {
    if (currentStationEl) currentStationEl.textContent = 'Locating current station…';
    if (nextEtaEl) nextEtaEl.textContent = 'Tracking route progression…';
    if (listEl) listEl.innerHTML = '<div class="timeline-loading-spinner" style="padding:28px 16px;text-align:center;color:#64748b;font-size:13px;">Loading route stations…</div>';
  }

  try {
    let stops = routeStopsCache.get(queryRoute);
    if (!stops || !stops.length) {
      const data = await get('/transit/routes/' + encodeURIComponent(queryRoute) + '/stops');
      stops = data.stops || [];
      if (stops.length) {
        routeStopsCache.set(queryRoute, stops);
      }
    }

    if (!stops.length) {
      if (currentStationEl) currentStationEl.textContent = 'Route details available';
      if (nextEtaEl) nextEtaEl.textContent = 'No sequential stops found for this corridor.';
      if (listEl) listEl.innerHTML = '<div style="padding:24px;text-align:center;color:#64748b;">Station list is currently unavailable for this corridor.</div>';
      return;
    }

    // Filter all active buses on this route
    const cleanQ = String(queryRoute).trim().toUpperCase();
    const cleanC = String(routeCode).trim().toUpperCase();
    const activeRouteBuses = vehicles.filter(v =>
      !v.stale && (
        v.route_id === cleanQ ||
        v.route_id === cleanC ||
        (v.route_id && String(v.route_id).toUpperCase() === cleanQ) ||
        (v.route_id && String(v.route_id).toUpperCase() === cleanC)
      )
    );

    // Reference point: pickup coordinates or user location
    let refLat = 28.6315, refLon = 77.2167;
    if (window.selectedPickupCoordinates) {
      [refLat, refLon] = window.selectedPickupCoordinates.split(',').map(Number);
    } else if (window.pickupCoordinates) {
      [refLat, refLon] = window.pickupCoordinates.split(',').map(Number);
    }

    // Sort active buses by proximity to the pickup/user location
    activeRouteBuses.sort((a, b) => {
      const da = haversineM(refLat, refLon, a.lat, a.lon);
      const db = haversineM(refLat, refLon, b.lat, b.lon);
      return da - db;
    });

    const nearestBus = activeRouteBuses.length > 0 ? activeRouteBuses[0] : null;

    if (!vehicle && nearestBus) {
      vehicle = nearestBus;
      currentTrackedVehicle = nearestBus;
    }

    // Update fleet chips in the UI
    const fleetChipsEl = $('tracker-fleet-chips');
    const fleetCountEl = $('tracker-fleet-count');
    if (fleetCountEl) {
      fleetCountEl.textContent = activeRouteBuses.length + (activeRouteBuses.length === 1 ? ' active bus' : ' active buses');
    }

    if (fleetChipsEl) {
      fleetChipsEl.replaceChildren();
      if (!activeRouteBuses.length) {
        fleetChipsEl.innerHTML = '<p class="fleet-empty-note">No live GPS broadcast right now. Showing scheduled route stops.</p>';
      } else {
        activeRouteBuses.forEach((b, idx) => {
          const isNearest = idx === 0;
          const isSelected = vehicle && (vehicle.id === b.id);
          const distToUser = haversineM(refLat, refLon, b.lat, b.lon);
          const distLabel = distToUser < 1000 ? Math.round(distToUser) + 'm' : (distToUser / 1000).toFixed(1) + ' km';
          const isAC = b.bus_type === 'ac';

          const chip = element('div', '');
          chip.className = `fleet-bus-chip ${isNearest ? 'nearest' : ''} ${isSelected ? 'selected' : ''}`;
          chip.innerHTML = `
            <div class="fleet-chip-top">
              <span class="fleet-bus-id">${isAC ? '❄ ' : '🚌 '}Bus #${b.id}</span>
              <div class="fleet-badges-wrap">
                ${isNearest ? '<span class="nearest-badge">⭐ NEAREST</span>' : ''}
                ${isSelected ? '<span class="tracking-badge">● Tracking</span>' : ''}
              </div>
            </div>
            <div class="fleet-chip-bottom">
              <span>📍 ~${distLabel} from pickup</span>
              <span>⚡ Live GPS</span>
            </div>
          `;

          chip.onclick = () => {
            openBusTracker(b, routeId, routeName, currentTrackedSegments, true);
          };

          fleetChipsEl.append(chip);
        });
      }
    }

    let currentIdx = 0;
    let minDist = Infinity;

    if (vehicle && vehicle.lat && vehicle.lon) {
      stops.forEach((s, idx) => {
        const d = haversineM(vehicle.lat, vehicle.lon, s.stop_lat, s.stop_lon);
        if (d < minDist) {
          minDist = d;
          currentIdx = idx;
        }
      });
    } else {
      const matchBus = vehicles.find(v => !v.stale && (v.route_id === queryRoute || v.route_id === routeCode));
      if (matchBus) {
        vehicle = matchBus;
        stops.forEach((s, idx) => {
          const d = haversineM(matchBus.lat, matchBus.lon, s.stop_lat, s.stop_lon);
          if (d < minDist) {
            minDist = d;
            currentIdx = idx;
          }
        });
      }
    }

    const currentStop = stops[currentIdx];
    if (currentStationEl) currentStationEl.textContent = currentStop.stop_name;

    if (nextEtaEl) {
      if (minDist < 120) {
        nextEtaEl.innerHTML = `<span class="eta-pill here">● Bus is at this stop</span> · Stop ${currentIdx + 1} of ${stops.length}`;
      } else if (minDist < 600) {
        nextEtaEl.innerHTML = `<span class="eta-pill arriving">● Approaching station</span> (~${Math.round(minDist)}m) · Stop ${currentIdx + 1} of ${stops.length}`;
      } else if (minDist < Infinity) {
        nextEtaEl.innerHTML = `<span class="eta-pill enroute">● Between stops</span> (~${(minDist / 1000).toFixed(1)} km away) · Stop ${currentIdx + 1} of ${stops.length}`;
      } else {
        nextEtaEl.innerHTML = `Stop 1 of ${stops.length} · ${stops.length} stops along route`;
      }
    }

    // IN-PLACE SMOOTH UPDATE: If the sheet is already open with the full stop list, update in place!
    const existingNodes = listEl ? listEl.querySelectorAll('.station-node') : [];
    if (isAlreadyOpen && existingNodes.length === stops.length) {
      existingNodes.forEach((node, idx) => {
        const isPassed = idx < currentIdx;
        const isCurrent = idx === currentIdx;
        const isUpcoming = idx > currentIdx;

        node.className = `station-node ${isPassed ? 'passed' : ''} ${isCurrent ? 'current' : ''} ${isUpcoming ? 'upcoming' : ''}`;
        if (isCurrent) {
          node.id = 'current-station-node';
        } else if (node.id === 'current-station-node') {
          node.removeAttribute('id');
        }

        const lineTop = node.querySelector('.station-track-line.top');
        if (lineTop) {
          lineTop.className = `station-track-line top ${idx === 0 ? 'hidden' : ''} ${isPassed || isCurrent ? 'passed-line' : ''}`;
        }

        const pinWrap = node.querySelector('.station-pin-wrap');
        if (pinWrap) {
          if (isCurrent) {
            pinWrap.innerHTML = `<div class="station-bus-pin" title="Bus is here"><span class="pin-bus-icon">🚌</span></div>`;
          } else if (isPassed) {
            pinWrap.innerHTML = `<div class="station-check-dot">✓</div>`;
          } else {
            pinWrap.innerHTML = `<div class="station-upcoming-dot"></div>`;
          }
        }

        const lineBottom = node.querySelector('.station-track-line.bottom');
        if (lineBottom) {
          lineBottom.className = `station-track-line bottom ${idx === stops.length - 1 ? 'hidden' : ''} ${isPassed ? 'passed-line' : ''}`;
        }

        const badgesWrap = node.querySelector('.station-badges-wrap');
        if (badgesWrap) {
          const oldStatusPill = badgesWrap.querySelector('.station-status-pill');
          if (oldStatusPill) oldStatusPill.remove();

          if (isCurrent) {
            const badge = element('span', 'HERE NOW');
            badge.className = 'station-status-pill current-pill';
            badgesWrap.append(badge);
          } else if (isPassed) {
            const badge = element('span', 'Departed');
            badge.className = 'station-status-pill passed-pill';
            badgesWrap.append(badge);
          }
        }

        const metaRow = node.querySelector('.station-meta-row');
        if (metaRow) {
          const seqText = `Stop ${idx + 1}`;
          let distText = '';
          if (isCurrent) {
            distText = minDist < 150 ? '· At platform' : `· ~${Math.round(minDist)}m`;
          } else if (isUpcoming && minDist < Infinity) {
            const stopsAway = idx - currentIdx;
            distText = `· ~${stopsAway * 3} min away`;
          }
          metaRow.textContent = `${seqText} ${distText}`;
        }
      });
      // NOTICE: In-place update complete! NO scrollIntoView, NO scrollTop=0, NO reloading screen!
    } else {
      // INITIAL FULL RENDER (Only executed on first open of this corridor)
      if (listEl) {
        listEl.replaceChildren();

        // Build crowd level lookup from journey segments if available
        const crowdMap = new Map();
        if (Array.isArray(currentTrackedSegments)) {
          currentTrackedSegments.forEach(seg => {
            if (seg.from) crowdMap.set(String(seg.from).trim().toLowerCase(), seg.level);
            if (seg.to) crowdMap.set(String(seg.to).trim().toLowerCase(), seg.level);
          });
        }

        stops.forEach((stop, idx) => {
          const isPassed = idx < currentIdx;
          const isCurrent = idx === currentIdx;
          const isUpcoming = idx > currentIdx;

          const node = element('div', '');
          node.className = `station-node ${isPassed ? 'passed' : ''} ${isCurrent ? 'current' : ''} ${isUpcoming ? 'upcoming' : ''}`;
          if (isCurrent) node.id = 'current-station-node';

          // Track column with line and indicator
          const trackCol = element('div', '');
          trackCol.className = 'station-track-col';

          const lineTop = element('div', '');
          lineTop.className = `station-track-line top ${idx === 0 ? 'hidden' : ''} ${isPassed || isCurrent ? 'passed-line' : ''}`;

          const pinWrapper = element('div', '');
          pinWrapper.className = 'station-pin-wrap';
          if (isCurrent) {
            pinWrapper.innerHTML = `<div class="station-bus-pin" title="Bus is here"><span class="pin-bus-icon">🚌</span></div>`;
          } else if (isPassed) {
            pinWrapper.innerHTML = `<div class="station-check-dot">✓</div>`;
          } else {
            pinWrapper.innerHTML = `<div class="station-upcoming-dot"></div>`;
          }

          const lineBottom = element('div', '');
          lineBottom.className = `station-track-line bottom ${idx === stops.length - 1 ? 'hidden' : ''} ${isPassed ? 'passed-line' : ''}`;

          trackCol.append(lineTop, pinWrapper, lineBottom);

          // Details column
          const detailsCol = element('div', '');
          detailsCol.className = 'station-details-col';

          const titleRow = element('div', '');
          titleRow.className = 'station-title-row';

          const nameEl = element('h4', stop.stop_name);
          nameEl.className = 'station-name';
          titleRow.append(nameEl);

          const stopKey = String(stop.stop_name).trim().toLowerCase();
          const crowdLvl = crowdMap.get(stopKey);
          let crowdPill = '';
          if (crowdLvl) {
            const lbl = crowdLvl === 'seats' ? 'Seats likely' : (crowdLvl === 'standing' ? 'Standing' : 'Crowded');
            crowdPill = `<span class="station-crowd-tag ${crowdLvl}">● ${lbl}</span>`;
          }

          const badgesWrap = element('div', '');
          badgesWrap.className = 'station-badges-wrap';
          if (crowdPill) {
            const cSpan = document.createElement('span');
            cSpan.innerHTML = crowdPill;
            badgesWrap.append(cSpan);
          }

          if (isCurrent) {
            const badge = element('span', 'HERE NOW');
            badge.className = 'station-status-pill current-pill';
            badgesWrap.append(badge);
          } else if (isPassed) {
            const badge = element('span', 'Departed');
            badge.className = 'station-status-pill passed-pill';
            badgesWrap.append(badge);
          }
          titleRow.append(badgesWrap);

          const metaRow = element('div', '');
          metaRow.className = 'station-meta-row';
          const seqText = `Stop ${idx + 1}`;
          let distText = '';
          if (isCurrent) {
            distText = minDist < 150 ? '· At platform' : `· ~${Math.round(minDist)}m`;
          } else if (isUpcoming && minDist < Infinity) {
            const stopsAway = idx - currentIdx;
            distText = `· ~${stopsAway * 3} min away`;
          }
          metaRow.append(element('span', `${seqText} ${distText}`));

          // Departures timetable expandable toggle
          const depToggle = element('button', 'Scheduled departures ›');
          depToggle.type = 'button';
          depToggle.className = 'btn-station-times';

          const depContainer = element('div', '');
          depContainer.className = 'station-departures-box';
          depContainer.hidden = true;

          depToggle.onclick = async (e) => {
            e.stopPropagation();
            if (!depContainer.hidden) {
              depContainer.hidden = true;
              depToggle.textContent = 'Scheduled departures ›';
              return;
            }
            depContainer.hidden = false;
            depToggle.textContent = 'Hide departures ‹';
            depContainer.innerHTML = '<div style="font-size:11.5px;color:#64748b;">Checking departures…</div>';
            try {
              const res = await get('/transit/stops/' + encodeURIComponent(stop.stop_id) + '/departures');
              depContainer.replaceChildren();
              if (!res.departures || !res.departures.length) {
                depContainer.append(element('p', 'No scheduled departures in the next 2 hours.'));
              } else {
                res.departures.slice(0, 6).forEach(dep => {
                  const row = element('div', '');
                  row.className = 'mini-dep-row';
                  row.innerHTML = `<strong>Route ${dep.route}</strong><span>In ${Math.ceil(dep.minutes)} min</span>`;
                  depContainer.append(row);
                });
              }
            } catch (err) {
              depContainer.textContent = 'Departures unavailable';
            }
          };

          detailsCol.append(titleRow, metaRow, depToggle, depContainer);
          node.append(trackCol, detailsCol);
          listEl.append(node);
        });

        // Auto-scroll to current station ONLY on initial open
        setTimeout(() => {
          const curr = $('current-station-node');
          if (curr) {
            curr.scrollIntoView({ behavior: 'smooth', block: 'center' });
          }
        }, 250);
      }
    }

    startAutoTrackerPolling();
  } catch (err) {
    if (currentStationEl) currentStationEl.textContent = 'Route details';
    if (nextEtaEl) nextEtaEl.textContent = 'Timeline will update when route details are loaded.';
    if (listEl && !isAlreadyOpen) {
      listEl.innerHTML = `<div style="padding:24px;text-align:center;color:#64748b;">Could not load station timeline: ${err.message}</div>`;
    }
  }
}

// -------------------------------------------------------------
// Auto-Streaming Live Bus Tracker (Smooth Background Polling)
// -------------------------------------------------------------
let trackerAutoTimer = null;

function startAutoTrackerPolling() {
  stopAutoTrackerPolling();
  trackerAutoTimer = setInterval(async () => {
    const sheet = $('bus-tracker-sheet');
    if (!sheet || sheet.hidden) {
      stopAutoTrackerPolling();
      return;
    }
    try {
      const d = await get('/vehicles');
      vehicles = d.vehicles || [];
      paint();
      if (currentTrackedVehicle && currentTrackedVehicle.id) {
        const found = vehicles.find(v => v.id === currentTrackedVehicle.id);
        if (found) currentTrackedVehicle = found;
      } else if (currentTrackedRouteId || currentTrackedRouteName) {
        const rCode = currentTrackedRouteName || currentTrackedRouteId;
        const found = vehicles.find(v => !v.stale && (v.route_id === currentTrackedRouteId || v.route_id === rCode));
        if (found) currentTrackedVehicle = found;
      }
      await openBusTracker(currentTrackedVehicle, currentTrackedRouteId, currentTrackedRouteName, currentTrackedSegments, true);
    } catch (_) { }
  }, 1500);
}

function stopAutoTrackerPolling() {
  if (trackerAutoTimer) {
    clearInterval(trackerAutoTimer);
    trackerAutoTimer = null;
  }
}

const closeTrackerBtn = $('close-tracker-btn');
if (closeTrackerBtn) {
  closeTrackerBtn.onclick = () => {
    const sheet = $('bus-tracker-sheet');
    if (sheet) {
      sheet.hidden = true;
      delete sheet.dataset.trackedRoute;
    }
    stopAutoTrackerPolling();
  };
}

const trackerMapHeaderBtn = $('btn-tracker-map-header');
if (trackerMapHeaderBtn) {
  trackerMapHeaderBtn.onclick = () => {
    const sheet = $('bus-tracker-sheet');
    if (sheet) sheet.hidden = true;
    stopAutoTrackerPolling();
    drawFullRouteOnMap(currentTrackedRouteId, currentTrackedRouteName, currentTrackedVehicle);
  };
}

const trackerMapFullBtn = $('btn-tracker-view-full-map');
if (trackerMapFullBtn) {
  trackerMapFullBtn.onclick = () => {
    const sheet = $('bus-tracker-sheet');
    if (sheet) sheet.hidden = true;
    stopAutoTrackerPolling();
    drawFullRouteOnMap(currentTrackedRouteId, currentTrackedRouteName, currentTrackedVehicle);
  };
}

const trackerRefreshBtn = $('tracker-refresh-btn');
if (trackerRefreshBtn) {
  trackerRefreshBtn.onclick = async () => {
    trackerRefreshBtn.classList.add('refreshing');
    try {
      const d = await get('/vehicles');
      vehicles = d.vehicles || [];
      paint();
      if (currentTrackedVehicle && currentTrackedVehicle.id) {
        const found = vehicles.find(v => v.id === currentTrackedVehicle.id);
        if (found) currentTrackedVehicle = found;
      } else if (currentTrackedRouteId || currentTrackedRouteName) {
        const rCode = currentTrackedRouteName || currentTrackedRouteId;
        const found = vehicles.find(v => !v.stale && (v.route_id === currentTrackedRouteId || v.route_id === rCode));
        if (found) currentTrackedVehicle = found;
      }
      await openBusTracker(currentTrackedVehicle, currentTrackedRouteId, currentTrackedRouteName, currentTrackedSegments, true);
    } catch (err) {
      console.warn('Tracker refresh error:', err);
    } finally {
      setTimeout(() => trackerRefreshBtn.classList.remove('refreshing'), 450);
    }
  };
}

// -------------------------------------------------------------
// PWA Install Prompt Listener
// -------------------------------------------------------------
let deferredPwaPrompt = null;
window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferredPwaPrompt = e;
  const installBtn = $('btn-install-pwa');
  if (installBtn) {
    installBtn.hidden = false;
    installBtn.onclick = async () => {
      installBtn.hidden = true;
      if (deferredPwaPrompt) {
        deferredPwaPrompt.prompt();
        await deferredPwaPrompt.userChoice;
        deferredPwaPrompt = null;
      }
    };
  }
});

window.addEventListener('appinstalled', () => {
  const installBtn = $('btn-install-pwa');
  if (installBtn) installBtn.hidden = true;
  deferredPwaPrompt = null;
});

// -------------------------------------------------------------
// DTC Fare Calculator Helper (Delhi Transit Fares)
// -------------------------------------------------------------
function calculateDTCFare(distanceKm, busType) {
  const isAC = busType === 'ac';
  let standardFare = 15;
  let acFare = 25;
  const d = Math.max(distanceKm || 0, 1);

  if (d <= 4) {
    standardFare = 5;
    acFare = 10;
  } else if (d <= 10) {
    standardFare = 10;
    acFare = d <= 8 ? 15 : 20;
  } else {
    standardFare = 15;
    acFare = 25;
  }

  return {
    standard: standardFare,
    ac: acFare,
    fare: isAC ? acFare : standardFare,
    pink: 0
  };
}

// -------------------------------------------------------------
// Digital Bus Ticket Modal Controller (Chartr / One Delhi Style)
// -------------------------------------------------------------
let currentTicketData = null;

function openDigitalTicket(routeCode, fromStop, toStop, fareObj, isAC) {
  const modal = $('digital-ticket-modal');
  if (!modal) return;
  currentTicketData = { routeCode, fromStop, toStop, fareObj, isAC };

  safeSetText('ticket-route-num', routeCode || 'BUS');
  safeSetText('ticket-bus-type-tag', isAC ? '❄ Electric AC Bus' : 'Standard Non-AC Bus');
  safeSetText('ticket-from-stop', fromStop || 'Boarding Stop');
  safeSetText('ticket-to-stop', toStop || 'Destination Stop');

  const initialFare = isAC ? fareObj.ac : fareObj.standard;
  safeSetText('ticket-fare-val', '₹' + initialFare);
  safeSetText('ticket-booking-time', new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) + ', Today');
  safeSetText('ticket-hash-code', 'DTC-DEL-' + Math.floor(100000 + Math.random() * 900000));

  const standardRadio = document.querySelector('input[name="pass-type"][value="standard"]');
  if (standardRadio) standardRadio.checked = true;

  document.querySelectorAll('input[name="pass-type"]').forEach(r => {
    r.onchange = () => {
      const isPink = r.value === 'pink';
      const fareEl = $('ticket-fare-val');
      if (fareEl) {
        fareEl.textContent = isPink ? 'FREE' : ('₹' + (isAC ? fareObj.ac : fareObj.standard));
        fareEl.style.color = isPink ? '#be185d' : '#15803d';
      }
    };
  });

  modal.showModal();
}

const closeTicketBtn = $('close-ticket-modal');
if (closeTicketBtn) closeTicketBtn.onclick = () => $('digital-ticket-modal').close();
const saveTicketCloseBtn = $('btn-save-ticket-close');
if (saveTicketCloseBtn) saveTicketCloseBtn.onclick = () => $('digital-ticket-modal').close();

// -------------------------------------------------------------
// Active Ride Mode & Alighting Alerts
// Live Bus & Route Number Search Controller
const busSearchInput = $('bus-route-search');
const clearBusSearchBtn = $('clear-bus-route-search');
const busSearchResults = $('bus-search-results');
let busSearchTimer = null;
let busSearchVer = 0;

if (busSearchInput && busSearchResults) {
  busSearchInput.oninput = () => {
    clearTimeout(busSearchTimer);
    const q = busSearchInput.value.trim().toUpperCase();
    const ver = ++busSearchVer;
    if (clearBusSearchBtn) clearBusSearchBtn.hidden = !q.length;
    if (q.length < 1) {
      busSearchResults.replaceChildren();
      return;
    }

    busSearchTimer = setTimeout(async () => {
      try {
        // 1. Live buses client-side match
        const liveMatches = vehicles.filter(v =>
          !v.stale && (
            (v.id && String(v.id).toUpperCase().includes(q)) ||
            (v.route_id && String(v.route_id).toUpperCase().includes(q))
          )
        ).slice(0, 4);

        // 2. Network routes search from backend API
        let routeMatches = [];
        try {
          const res = await get('/transit/routes/search?q=' + encodeURIComponent(q));
          routeMatches = res.routes || [];
        } catch (_) { }

        if (ver !== busSearchVer) return;
        busSearchResults.replaceChildren();

        if (!liveMatches.length && !routeMatches.length) {
          const emptyMsg = element('p', 'No matching buses or routes found.');
          emptyMsg.className = 'search-empty-text';
          busSearchResults.append(emptyMsg);
          return;
        }

        // Live bus matches section
        if (liveMatches.length > 0) {
          const liveTitle = element('div', '● Live Running Buses');
          liveTitle.className = 'search-results-section-header live';
          busSearchResults.append(liveTitle);

          liveMatches.forEach(v => {
            const isAC = v.bus_type === 'ac';
            const cleanR = formatRoute(v.route_id || 'BUS');
            const item = element('div', '');
            item.className = 'bus-search-item';
            item.innerHTML = `
              <div class="search-item-left">
                <span class="search-rt-pill ${isAC ? 'ac' : ''}">${cleanR}</span>
                <div class="search-item-texts">
                  <strong>Bus #${v.id}</strong>
                  <small>${isAC ? '❄ AC Electric' : 'Standard'} · Active live</small>
                </div>
              </div>
              <div class="search-item-actions">
                <button type="button" class="btn-search-map-action" title="View route on map">🗺️ Map</button>
                <button type="button" class="btn-track-action" title="Track station by station">Track ➔</button>
              </div>
            `;
            const mapBtn = item.querySelector('.btn-search-map-action');
            if (mapBtn) {
              mapBtn.onclick = (e) => {
                e.stopPropagation();
                busSearchResults.replaceChildren();
                busSearchInput.value = '';
                if (clearBusSearchBtn) clearBusSearchBtn.hidden = true;
                drawFullRouteOnMap(v.route_id, cleanR, v);
              };
            }
            const trackBtn = item.querySelector('.btn-track-action');
            if (trackBtn) {
              trackBtn.onclick = (e) => {
                e.stopPropagation();
                busSearchResults.replaceChildren();
                busSearchInput.value = '';
                if (clearBusSearchBtn) clearBusSearchBtn.hidden = true;
                if (typeof recordUserSearchedRoute === 'function') recordUserSearchedRoute(v.route_id, 'Bus #' + v.id);
                openBusTracker(v, v.route_id, cleanR);
              };
            }
            item.onclick = () => {
              busSearchResults.replaceChildren();
              busSearchInput.value = '';
              if (clearBusSearchBtn) clearBusSearchBtn.hidden = true;
              drawFullRouteOnMap(v.route_id, cleanR, v);
            };
            busSearchResults.append(item);
          });
        }

        // Route corridors section
        if (routeMatches.length > 0) {
          const routesTitle = element('div', 'Route Corridors');
          routesTitle.className = 'search-results-section-header';
          busSearchResults.append(routesTitle);

          routeMatches.forEach(r => {
            const cleanR = formatRoute(r.route_short_name || r.route_id);
            const activeBus = vehicles.find(v => !v.stale && (v.route_id === r.route_id || v.route_id === cleanR || v.route_id === r.route_short_name));
            const item = element('div', '');
            item.className = 'bus-search-item';
            item.innerHTML = `
              <div class="search-item-left">
                <span class="search-rt-pill">${cleanR}</span>
                <div class="search-item-texts">
                  <strong>${r.route_desc || r.route_long_name || ('Route ' + cleanR)}</strong>
                  <small>${activeBus ? '● Active bus running' : 'Scheduled corridor'}</small>
                </div>
              </div>
              <div class="search-item-actions">
                <button type="button" class="btn-search-map-action" title="View route on map">🗺️ Map</button>
                <button type="button" class="btn-track-action" title="Track station by station">Track ➔</button>
              </div>
            `;
            const mapBtn = item.querySelector('.btn-search-map-action');
            if (mapBtn) {
              mapBtn.onclick = (e) => {
                e.stopPropagation();
                busSearchResults.replaceChildren();
                busSearchInput.value = '';
                if (clearBusSearchBtn) clearBusSearchBtn.hidden = true;
                drawFullRouteOnMap(r.route_id, cleanR, activeBus);
              };
            }
            const trackBtn = item.querySelector('.btn-track-action');
            if (trackBtn) {
              trackBtn.onclick = (e) => {
                e.stopPropagation();
                busSearchResults.replaceChildren();
                busSearchInput.value = '';
                if (clearBusSearchBtn) clearBusSearchBtn.hidden = true;
                if (typeof recordUserSearchedRoute === 'function') recordUserSearchedRoute(cleanR, r.route_desc);
                openBusTracker(activeBus, r.route_id, cleanR);
              };
            }
            item.onclick = () => {
              busSearchResults.replaceChildren();
              busSearchInput.value = '';
              if (clearBusSearchBtn) clearBusSearchBtn.hidden = true;
              drawFullRouteOnMap(r.route_id, cleanR, activeBus);
            };
            busSearchResults.append(item);
          });
        }
      } catch (err) {
        if (ver === busSearchVer) {
          busSearchResults.textContent = 'Search unavailable: ' + err.message;
        }
      }
    }, 200);
  };

  if (clearBusSearchBtn) {
    clearBusSearchBtn.onclick = () => {
      busSearchInput.value = '';
      clearBusSearchBtn.hidden = true;
      busSearchResults.replaceChildren();
      busSearchInput.focus();
    };
  }
}

// Native Animated Splash Landing Screen Controller
const splashEl = $('app-splash');
if (splashEl) {
  function dismissSplash() {
    if (!splashEl || splashEl.classList.contains('dismissed')) return;
    splashEl.classList.add('dismissed');
    setTimeout(() => {
      splashEl.remove();
      if (typeof map !== 'undefined' && map && map.invalidateSize) map.invalidateSize();
    }, 550);
  }

  // Auto-dismiss smoothly after exactly 2 seconds
  const splashTimer = setTimeout(dismissSplash, 2000);
  splashEl.addEventListener('click', dismissSplash);
}

const activeBusMarkers = new Map();

function paint() {
  const bounds = map.getBounds();
  const zoom = map.getZoom();

  const filterBar = $('active-route-filter-bar');
  if (filterBar) {
    if (filter) {
      filterBar.hidden = false;
      const count = vehicles.filter(v => v.route_id === filter).length;
      const filterText = $('filter-bar-text');
      if (filterText) filterText.textContent = `Route ${filter} (${count} live bus${count === 1 ? '' : 'es'})`;
    } else {
      filterBar.hidden = true;
    }
  }

  const fresh = vehicles.filter(v => !v.stale && (activeBusType === 'all' || v.bus_type === activeBusType) && (!filter || v.route_id === filter));
  const visibleIds = new Set();

  fresh.filter(v => bounds.contains([v.lat, v.lon])).forEach(v => {
    visibleIds.add(v.id);
    const isAC = v.bus_type === 'ac';
    const typeLabel = isAC ? '❄ Electric AC' : (v.bus_type === 'non-ac' ? 'Standard Non-AC' : 'DTC Bus');
    const updateTime = v.timestamp ? new Date(v.timestamp * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'Just now';

    let html, size, anchor;
    if (zoom >= 13) {
      html = `<div class="simple-bus-pill ${isAC ? 'ac' : ''}"><span class="bus-icon">🚌</span><span class="pill-rt">${v.route_id || 'BUS'}</span></div>`;
      size = [64, 24]; anchor = [32, 12];
    } else {
      html = `<span class="simple-bus-dot ${isAC ? 'ac' : ''}" aria-hidden="true"></span>`;
      size = [16, 16]; anchor = [8, 8];
    }

    const popupHtml = `
      <div class="bus-popup-header">
        <span class="bus-popup-badge ${isAC ? 'ac' : ''}">${v.route_id || 'BUS'}</span>
        <strong>${v.id}</strong>
      </div>
      <p class="bus-popup-meta">${typeLabel} · ${updateTime}</p>
      <button type="button" class="bus-track-action-btn">Track Station by Station ➔</button>
    `;

    if (activeBusMarkers.has(v.id)) {
      const marker = activeBusMarkers.get(v.id);
      marker.setLatLng([v.lat, v.lon]);
      marker.setIcon(L.divIcon({ className: 'simple-bus-marker-wrap', html: html, iconSize: size, iconAnchor: anchor }));
      const p = marker.getPopup();
      if (p) p.setContent(popupHtml);
    } else {
      const marker = L.marker([v.lat, v.lon], {
        icon: L.divIcon({ className: 'simple-bus-marker-wrap', html: html, iconSize: size, iconAnchor: anchor }),
        title: 'Bus ' + v.id + ' Route ' + (v.route_id || ''),
        alt: 'Bus ' + v.id
      });

      const p = element('div', '');
      p.className = 'bus-popup-card';
      p.innerHTML = popupHtml;

      p.onclick = (e) => {
        if (e.target.closest('.bus-track-action-btn')) {
          e.stopPropagation();
          openBusTracker(v, v.route_id, v.route_id);
        }
      };

      marker.bindPopup(p);
      marker.on('click', () => {
        openBusTracker(v, v.route_id, v.route_id);
      });

      marker.addTo(buses);
      activeBusMarkers.set(v.id, marker);
    }
  });

  for (const [id, marker] of activeBusMarkers.entries()) {
    if (!visibleIds.has(id)) {
      buses.removeLayer(marker);
      activeBusMarkers.delete(id);
    }
  }
}

async function refresh() {
  try {
    const d = await get('/vehicles');
    vehicles = d.vehicles || [];
    paint();
  } catch (e) {
    vehicles = []; paint();
  }
}
map.on('moveend', paint);
const clearFilterBtn = $('btn-clear-map-filter');
if (clearFilterBtn) {
  clearFilterBtn.onclick = () => {
    filter = null;
    paths.clearLayers();
    paint();
  };
}

function formatRoute(r) {
  if (!r) return '';
  return String(r)
    .replace(/(?:_|\s|-)*(?:UP|DOWN)\b/gi, '')
    .replace(/\s+/g, ' ')
    .trim();
}

async function drawFullRouteOnMap(routeId, routeName, activeBus) {
  const cleanR = formatRoute(routeName || routeId);
  const qRoute = routeId || cleanR;
  paths.clearLayers();
  filter = qRoute;
  paint();

  try {
    const [shapeRes, stopsRes] = await Promise.all([
      get('/transit/routes/' + encodeURIComponent(qRoute)).catch(() => ({ paths: [] })),
      get('/transit/routes/' + encodeURIComponent(qRoute) + '/stops').catch(() => ({ stops: [] }))
    ]);

    const shapePaths = (shapeRes && shapeRes.paths) || [];
    const stopsList = (stopsRes && stopsRes.stops) || [];

    // 1. Draw route polyline with dark casing + vibrant emerald inner line
    if (shapePaths.length > 0) {
      shapePaths.forEach(p => {
        L.polyline(p, {
          color: '#064e3b',
          weight: 7.5,
          opacity: 0.8,
          lineJoin: 'round',
          lineCap: 'round'
        }).addTo(paths);

        L.polyline(p, {
          color: '#10b981',
          weight: 4.5,
          opacity: 0.95,
          lineJoin: 'round',
          lineCap: 'round'
        }).addTo(paths);
      });
    }

    // 2. Intermediate bus stand markers with sequence badges
    if (stopsList.length > 0) {
      const totalStops = stopsList.length;
      stopsList.forEach((st, idx) => {
        if (idx === 0 || idx === totalStops - 1) return;
        const stopSeq = st.seq !== undefined ? st.seq : (idx + 1);

        const stationIcon = L.divIcon({
          className: 'map-station-stop-wrap',
          html: `
            <div class="map-station-marker" title="Bus Stand ${stopSeq}: ${st.stop_name}">
              <span class="station-marker-icon">🚏</span>
              <span class="station-seq-num">${stopSeq}</span>
            </div>
          `,
          iconSize: [28, 28],
          iconAnchor: [14, 14]
        });

        const stMarker = L.marker([st.stop_lat, st.stop_lon], { icon: stationIcon, zIndexOffset: 500 });
        stMarker.bindTooltip(`<strong>🚏 Stop ${stopSeq}:</strong> ${st.stop_name}`, {
          permanent: false,
          direction: 'top',
          offset: [0, -14],
          className: 'station-hover-tooltip'
        });
        stMarker.bindPopup(`
          <div class="map-station-popup">
            <span class="station-seq-badge">🚏 Bus Stand ${stopSeq} of ${totalStops}</span>
            <strong>${st.stop_name}</strong>
            <p class="station-route-meta">Route ${cleanR} · Passenger Stand</p>
          </div>
        `);
        stMarker.addTo(paths);
      });

      // 3. START Station: Prominent Green Location Pointer Pin
      const startStop = stopsList[0];
      const startPin = L.divIcon({
        className: 'journey-pointer-wrap',
        html: `
          <div class="route-location-pin start" title="Start / Boarding Stand: ${startStop.stop_name}">
            <div class="pin-marker-body">
              <span class="pin-logo">📍</span>
              <span class="pin-label">START: ${startStop.stop_name}</span>
            </div>
            <div class="pin-pointer-tip"></div>
            <div class="pin-pulse-ring"></div>
          </div>
        `,
        iconSize: [160, 48],
        iconAnchor: [80, 44]
      });

      L.marker([startStop.stop_lat, startStop.stop_lon], { icon: startPin, zIndexOffset: 2500 })
        .bindPopup(`
          <div class="map-station-popup">
            <span class="station-seq-badge" style="background:#dcfce7;color:#15803d;font-weight:800;">📍 START / BOARDING STAND</span>
            <strong>${startStop.stop_name}</strong>
            <p class="station-route-meta">Route ${cleanR} · Terminal Origin</p>
          </div>
        `)
        .addTo(paths);

      // 4. DESTINATION Station: Prominent Red Location Pointer Pin
      const endStop = stopsList[totalStops - 1];
      const endPin = L.divIcon({
        className: 'journey-pointer-wrap',
        html: `
          <div class="route-location-pin end" title="Destination Stand: ${endStop.stop_name}">
            <div class="pin-marker-body">
              <span class="pin-logo">🏁</span>
              <span class="pin-label">DESTINATION: ${endStop.stop_name}</span>
            </div>
            <div class="pin-pointer-tip"></div>
          </div>
        `,
        iconSize: [180, 48],
        iconAnchor: [90, 44]
      });

      L.marker([endStop.stop_lat, endStop.stop_lon], { icon: endPin, zIndexOffset: 2500 })
        .bindPopup(`
          <div class="map-station-popup">
            <span class="station-seq-badge" style="background:#fee2e2;color:#b91c1c;font-weight:800;">🏁 DESTINATION STAND</span>
            <strong>${endStop.stop_name}</strong>
            <p class="station-route-meta">Route ${cleanR} · Final Terminus</p>
          </div>
        `)
        .addTo(paths);
    }

    // 5. Fit bounds to full road path or stops
    const allCoords = [];
    if (shapePaths.length > 0) {
      shapePaths.forEach(p => p.forEach(pt => allCoords.push(pt)));
    } else if (stopsList.length > 0) {
      stopsList.forEach(s => allCoords.push([s.stop_lat, s.stop_lon]));
    }
    if (allCoords.length > 0) {
      map.fitBounds(L.latLngBounds(allCoords), { padding: [60, 60] });
    }

    const startName = stopsList.length > 0 ? stopsList[0].stop_name : 'Start Stand';
    const endName = stopsList.length > 0 ? stopsList[stopsList.length - 1].stop_name : 'Destination Stand';
    const numStops = stopsList.length;

    safeSetText('map-headline', 'Route ' + cleanR);
    safeSetText('map-description', `${startName} → ${endName} · ${numStops} bus stands along corridor`);

    const filterBar = $('active-route-filter-bar');
    if (filterBar) {
      filterBar.hidden = false;
      safeSetText('filter-bar-text', `Route ${cleanR}: ${startName} ➔ ${endName} (${numStops} stands)`);
    }

    const trackerSheet = $('bus-tracker-sheet');
    if (trackerSheet) trackerSheet.hidden = true;
    const stationSheet = $('station-explorer-sheet');
    if (stationSheet) stationSheet.hidden = true;

    if (typeof showScreen === 'function') showScreen('map');
    map.invalidateSize();
  } catch (err) {
    console.error('Error drawing route on map:', err);
  }
}
window.drawFullRouteOnMap = drawFullRouteOnMap;

async function openStationExplorer(stopId, stopName, distLabel) {
  const sheet = $('station-explorer-sheet');
  if (!sheet) return;
  safeSetText('station-explorer-title', stopName);
  safeSetText('station-explorer-meta', distLabel ? `~${distLabel} away` : 'Bus Stand');
  const container = $('station-departures-container');
  if (container) {
    container.innerHTML = '<div class="station-loading-spinner">Loading station buses & routes…</div>';
  }
  sheet.hidden = false;

  try {
    const res = await get('/transit/stops/' + encodeURIComponent(stopId) + '/departures');
    if (!container) return;
    container.replaceChildren();

    if (!res.departures || !res.departures.length) {
      container.innerHTML = '<div class="station-empty-msg">No scheduled departures in the next two hours. Check back soon.</div>';
      return;
    }

    res.departures.forEach(dep => {
      const cleanR = formatRoute(dep.route);
      const row = element('div', '');
      row.className = 'station-bus-row';
      const etaText = dep.minutes <= 1 ? 'Due now' : `In ${Math.ceil(dep.minutes)} min`;
      row.innerHTML = `
        <div class="station-bus-info">
          <div class="station-bus-badge">${cleanR}</div>
          <div class="station-bus-timing">
            <strong>${etaText}</strong>
            <small>${dep.departure ? (dep.departure.slice(11, 16) || dep.departure) : 'Scheduled'}</small>
          </div>
        </div>
        <button type="button" class="btn-draw-station-route">🗺️ View Route</button>
      `;

      const viewRouteBtn = row.querySelector('.btn-draw-station-route');
      if (viewRouteBtn) {
        viewRouteBtn.onclick = (e) => {
          e.stopPropagation();
          sheet.hidden = true;
          drawFullRouteOnMap(dep.route_id || cleanR, cleanR);
        };
      }

      container.append(row);
    });
  } catch (err) {
    if (container) {
      container.innerHTML = `<div class="station-empty-msg">Could not load timetable: ${err.message}</div>`;
    }
  }
}

const closeStationExplorerBtn = $('btn-close-station-explorer');
if (closeStationExplorerBtn) {
  closeStationExplorerBtn.onclick = () => {
    const sheet = $('station-explorer-sheet');
    if (sheet) sheet.hidden = true;
  };
}

async function nearby(lat, lon) {
  const id = ++sequence;
  safeSetText('nearby-location', 'Finding nearby stops…');
  try {
    const d = await get('/transit/stops?' + new URLSearchParams({ lat, lon }));
    if (id !== sequence) return;
    stands.clearLayers();
    const host = $('nearby-stands');
    if (!host) return;
    host.replaceChildren();
    safeSetText('nearby-location', 'Within 2 km · distances measured in a straight line');
    if (!d.stops.length) {
      host.append(element('p', 'No stops nearby. Try another area on the map.'));
      return;
    }
    d.stops.forEach((s) => {
      const distLabel = s.distance_m >= 1000 ? (s.distance_m / 1000).toFixed(1) + ' km' : Math.round(s.distance_m) + ' m';
      L.circleMarker([s.lat, s.lon], {
        radius: 6,
        color: '#164c36',
        fillColor: '#ffffff',
        fillOpacity: 1,
        weight: 2.5
      }).bindPopup(element('strong', s.name)).addTo(stands);

      const card = element('article', '');
      card.className = 'clean-nearby-stop-card';
      card.innerHTML = `
        <div class="stop-card-main">
          <div class="stop-icon-col">🚏</div>
          <div class="stop-info-col">
            <h3 class="stop-clean-name">${s.name}</h3>
            <span class="stop-clean-dist">📍 ~${distLabel} away</span>
          </div>
          <div class="stop-chevron-col">
            <span class="view-buses-pill">Buses & Routes ›</span>
          </div>
        </div>
      `;
      card.onclick = () => {
        openStationExplorer(s.id, s.name, distLabel);
      };
      host.append(card);
    });
  } catch (e) { if (id === sequence) safeSetText('nearby-location', e.message); }
}
$('locate-me').onclick = () => { if (!navigator.geolocation) return; $('nearby-location').textContent = 'Requesting your location…'; navigator.geolocation.getCurrentPosition(p => { map.setView([p.coords.latitude, p.coords.longitude], 15); nearby(p.coords.latitude, p.coords.longitude); }, () => { $('nearby-location').textContent = 'Location unavailable. Tap the map to choose a location.'; }, { timeout: 12000 }); };
$('demo-location').textContent = 'Search map area'; $('demo-location').onclick = () => { filter = null; paths.clearLayers(); paint(); const c = map.getCenter(); nearby(c.lat, c.lng); }; map.on('click', e => nearby(e.latlng.lat, e.latlng.lng));
$('zoom-in').onclick = () => map.zoomIn(); $('zoom-out').onclick = () => map.zoomOut(); $('fit').onclick = () => { filter = null; paths.clearLayers(); map.setView([28.6315, 77.2167], 13); paint(); };
refresh(); setInterval(() => { if (!document.hidden) refresh(); }, 1500); nearby(28.6315, 77.2167);
const readSaved = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } };
const saveLocal = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; } };
let recent = readSaved('transit.recents', []); if (!Array.isArray(recent)) recent = [];
let destinationMarker = null;
function selectDestination(s) {
  window.destinationCoordinates = s.stop_lat + ',' + s.stop_lon;
  window.destinationName = s.stop_name;
  $('destination-search').value = s.stop_name;
  $('destination-results').replaceChildren();
  recent = [s, ...recent.filter(x => x.stop_id !== s.stop_id)].slice(0, 5);
  saveLocal('transit.recents', recent);
  renderRecent();
  if (destinationMarker) map.removeLayer(destinationMarker);
  destinationMarker = L.marker([s.stop_lat, s.stop_lon]).addTo(map).bindPopup(element('strong', s.stop_name)).openPopup();
  map.setView([s.stop_lat, s.stop_lon], 15);
  nearby(s.stop_lat, s.stop_lon);
  safeSetText('map-headline', 'Destination: ' + s.stop_name);
  $('pickup-label').textContent = pickupText;
}
function renderRecent() {
  const wrapper = $('recent-destinations-wrapper');
  if (wrapper) wrapper.hidden = !recent.length;
  const host = $('recent-destinations');
  if (!host) return;
  host.replaceChildren();
  const clearBtn = $('clear-recents');
  if (clearBtn) clearBtn.hidden = !recent.length;
  if (!recent.length) {
    if (wrapper) wrapper.hidden = true;
    return;
  }
  recent.forEach(s => {
    if (!s || !s.stop_name) return;
    const b = element('button', '◷  ' + s.stop_name);
    b.className = 'recent-item';
    b.onclick = () => selectDestination(s);
    host.append(b);
  });
}
$('clear-recents').onclick = () => { recent = []; saveLocal('transit.recents', recent); renderRecent(); };

// Location Search Overlay Controller (From & To)
let currentLocPickerMode = 'pickup';
function openLocationPicker(mode) {
  currentLocPickerMode = mode;
  const overlay = $('location-search-screen');
  if (!overlay) return;
  overlay.hidden = false;
  safeSetText('loc-search-header-title', mode === 'pickup' ? 'Select Pickup Station' : 'Select Destination Station');
  const input = $('loc-search-input');
  if (input) {
    input.value = '';
    input.placeholder = mode === 'pickup' ? 'Search pickup stop (e.g. Anand Vihar, AIIMS…)' : 'Search destination stop (e.g. Kashmere Gate…)';
    input.focus();
  }
  const clearBtn = $('loc-search-clear-btn');
  if (clearBtn) clearBtn.hidden = true;
  const list = $('loc-search-results-list');
  if (list) list.replaceChildren();
}

function closeLocationPicker() {
  const overlay = $('location-search-screen');
  if (overlay) overlay.hidden = true;
}

const locBackBtn = $('loc-search-back-btn');
if (locBackBtn) locBackBtn.onclick = closeLocationPicker;

const locClearBtn = $('loc-search-clear-btn');
if (locClearBtn) {
  locClearBtn.onclick = () => {
    const input = $('loc-search-input');
    if (input) input.value = '';
    locClearBtn.hidden = true;
    const list = $('loc-search-results-list');
    if (list) list.replaceChildren();
  };
}

let locPickerTimer;
const locInput = $('loc-search-input');
if (locInput) {
  locInput.oninput = () => {
    clearTimeout(locPickerTimer);
    const q = locInput.value.trim();
    if (locClearBtn) locClearBtn.hidden = !q;
    const list = $('loc-search-results-list');
    if (!list) return;
    if (q.length < 2) {
      list.replaceChildren();
      return;
    }
    locPickerTimer = setTimeout(async () => {
      try {
        const d = await get('/transit/search?q=' + encodeURIComponent(q));
        list.replaceChildren();
        if (!d.stops || !d.stops.length) {
          list.innerHTML = '<div class="loc-search-empty">No matching bus stops found. Try another spelling.</div>';
          return;
        }
        d.stops.forEach(s => {
          const item = element('button', '');
          item.type = 'button';
          item.className = 'loc-result-item';
          item.innerHTML = `
            <span class="loc-result-icon">🚏</span>
            <div class="loc-result-text">
              <strong>${s.stop_name}</strong>
              <small>Delhi Bus Stand</small>
            </div>
            <span class="loc-result-arrow">›</span>
          `;
          item.onclick = () => {
            if (currentLocPickerMode === 'pickup') {
              $('pickup-search').value = s.stop_name;
              window.pickupCoordinates = s.stop_lat + ',' + s.stop_lon;
              window.pickupName = s.stop_name;
              pickupText = s.stop_name;
              $('pickup-label').textContent = 'Pickup selected';
              if (pickupMarker) map.removeLayer(pickupMarker);
              pickupMarker = L.circleMarker([s.stop_lat, s.stop_lon], { radius: 9, color: '#fff', fillColor: '#10b981', fillOpacity: 1, weight: 3 }).addTo(map).bindPopup(element('strong', 'Pickup: ' + s.stop_name));
            } else {
              selectDestination(s);
            }
            closeLocationPicker();
          };
          list.append(item);
        });
      } catch (_) {
        list.innerHTML = '<div class="loc-search-empty">Could not load search results. Please try again.</div>';
      }
    }, 200);
  };
}

const pickerGpsBtn = $('btn-picker-use-gps');
if (pickerGpsBtn) {
  pickerGpsBtn.onclick = () => {
    if (!navigator.geolocation) return;
    pickerGpsBtn.disabled = true;
    safeSetText('loc-search-header-title', 'Locating your GPS position…');
    navigator.geolocation.getCurrentPosition(
      p => {
        pickerGpsBtn.disabled = false;
        const lat = p.coords.latitude;
        const lon = p.coords.longitude;
        if (currentLocPickerMode === 'pickup') {
          $('pickup-search').value = 'Current location';
          window.pickupCoordinates = `${lat},${lon}`;
          window.pickupName = 'Current location';
          pickupText = 'Current location';
          $('pickup-label').textContent = 'Current location selected';
        } else {
          $('destination-search').value = 'Current location';
          window.destinationCoordinates = `${lat},${lon}`;
          window.destinationName = 'Current location';
        }
        closeLocationPicker();
      },
      () => {
        pickerGpsBtn.disabled = false;
        safeSetText('loc-search-header-title', 'Location unavailable');
      },
      { timeout: 10000 }
    );
  };
}

// Wire From and To inputs to open overlay without disrupting UI
$('pickup-search').onclick = () => openLocationPicker('pickup');
$('destination-search').onclick = () => openLocationPicker('dest');

// Wire Departure shortcut "Leave Now" button
const btnLeaveNow = $('btn-leave-now');
if (btnLeaveNow) {
  btnLeaveNow.onclick = () => {
    const now = new Date();
    const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
    $('rider-departure').value = local;
  };
}

// Wire Full Map View filters & back button
document.querySelectorAll('.btn-map-filter-chip').forEach(btn => {
  btn.onclick = () => {
    document.querySelectorAll('.btn-map-filter-chip').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const f = btn.getAttribute('data-filter');
    activeBusType = f;
    paint();
  };
});
const backToPlannerBtn = $('btn-back-to-planner');
if (backToPlannerBtn && typeof showScreen === 'function') {
  backToPlannerBtn.onclick = () => showScreen('plan');
}

let pickupText = 'Fetch my location';
let pickupTimer, pickupVersion = 0, pickupMarker = null;
$('pickup-search').oninput = () => {
  clearTimeout(pickupTimer); const q = $('pickup-search').value.trim(), version = ++pickupVersion;
  $('pickup-results').replaceChildren(); $('pickup-label').textContent = 'Select a matching pickup stop';
  if (q.length < 2) return;
  pickupTimer = setTimeout(async () => {
    try {
      const d = await get('/transit/search?q=' + encodeURIComponent(q));
      if (version !== pickupVersion) return;
      const host = $('pickup-results'); host.replaceChildren();
      if (!d.stops.length) host.append(element('p', 'No matching stops. Try another stop name.'));
      d.stops.forEach(s => {
        const b = element('button', s.stop_name); b.className = 'destination-option'; b.type = 'button';
        b.onclick = () => {
          pickupVersion++; window.selectedPickupCoordinates = s.stop_lat + ',' + s.stop_lon;
          window.selectedPickupName = s.stop_name; pickupText = s.stop_name;
          $('pickup-search').value = s.stop_name; $('pickup-label').textContent = 'Pickup selected';
          host.replaceChildren();
          if (pickupMarker) map.removeLayer(pickupMarker);
          pickupMarker = L.circleMarker([s.stop_lat, s.stop_lon], { radius: 9, color: '#fff', fillColor: '#10b981', fillOpacity: 1, weight: 3 }).addTo(map).bindPopup(element('strong', 'Pickup: ' + s.stop_name));
          map.setView([s.stop_lat, s.stop_lon], 15); nearby(s.stop_lat, s.stop_lon);
        };
        host.append(b);
      });
    } catch { if (version === pickupVersion) $('pickup-results').textContent = 'Pickup search unavailable. Please try again.'; }
  }, 250);
};
const fetchPickup = () => {
  if (!navigator.geolocation) { $('pickup-label').textContent = 'Location unavailable'; return; }
  $('pickup-label').textContent = 'Finding you…';
  navigator.geolocation.getCurrentPosition(p => {
    window.pickupCoordinates = p.coords.latitude + ',' + p.coords.longitude;
    pickupText = 'Current location'; $('pickup-search').value = 'Current location';
    $('pickup-results').replaceChildren(); $('pickup-label').textContent = pickupText;
    map.setView([p.coords.latitude, p.coords.longitude], 15);
    if (pickupMarker) map.removeLayer(pickupMarker);
    pickupMarker = L.circleMarker([p.coords.latitude, p.coords.longitude], { radius: 9, color: '#fff', fillColor: '#10b981', fillOpacity: 1, weight: 3 }).addTo(map).bindPopup('Your location');
    nearby(p.coords.latitude, p.coords.longitude);
  }, () => { $('pickup-label').textContent = 'Location denied or unavailable'; }, { timeout: 12000, maximumAge: 30000 });
};
$('fetch-location').onclick = $('locate-me').onclick = () => { pickupVersion++; clearTimeout(pickupTimer); $('pickup-results').replaceChildren(); fetchPickup(); };

// Smart Swap Origin & Destination
const swapBtn = $('swap-locations');
if (swapBtn) {
  swapBtn.onclick = () => {
    swapBtn.classList.add('rotating');
    setTimeout(() => swapBtn.classList.remove('rotating'), 350);
    const prevPickVal = $('pickup-search').value;
    const prevDestVal = $('destination-search').value;
    const prevPickCoord = window.selectedPickupCoordinates || window.pickupCoordinates;
    const prevPickName = window.selectedPickupName;
    const prevDestCoord = window.destinationCoordinates;
    const prevDestName = window.destinationName;

    $('pickup-search').value = prevDestVal;
    $('destination-search').value = prevPickVal;

    window.selectedPickupCoordinates = prevDestCoord;
    window.selectedPickupName = prevDestName;
    window.destinationCoordinates = prevPickCoord;
    window.destinationName = prevPickName;

    if (window.selectedPickupName) $('pickup-label').textContent = 'Pickup selected';
    if (window.selectedPickupCoordinates) {
      const [plat, plon] = window.selectedPickupCoordinates.split(',').map(Number);
      if (pickupMarker) map.removeLayer(pickupMarker);
      pickupMarker = L.circleMarker([plat, plon], { radius: 9, color: '#fff', fillColor: '#10b981', fillOpacity: 1, weight: 3 }).addTo(map).bindPopup(element('strong', 'Pickup: ' + (window.selectedPickupName || '')));
      nearby(plat, plon);
    }
    if (window.destinationCoordinates) {
      const [dlat, dlon] = window.destinationCoordinates.split(',').map(Number);
      if (destinationMarker) map.removeLayer(destinationMarker);
      destinationMarker = L.marker([dlat, dlon]).addTo(map).bindPopup(element('strong', window.destinationName || '')).openPopup();
    }
  };
}

// Most Searched Routes by User Controller
let userSearchedRoutes = readSaved('transit.userSearchedRoutes', []);
if (!Array.isArray(userSearchedRoutes)) userSearchedRoutes = [];

function recordUserSearchedRoute(routeCode, routeDesc) {
  if (!routeCode) return;
  const cleanCode = String(routeCode).trim().toUpperCase();
  const existingIdx = userSearchedRoutes.findIndex(r => r.code === cleanCode);
  if (existingIdx >= 0) {
    userSearchedRoutes[existingIdx].count = (userSearchedRoutes[existingIdx].count || 1) + 1;
    userSearchedRoutes[existingIdx].lastUsed = Date.now();
    if (routeDesc && !userSearchedRoutes[existingIdx].desc) {
      userSearchedRoutes[existingIdx].desc = routeDesc;
    }
    const item = userSearchedRoutes.splice(existingIdx, 1)[0];
    userSearchedRoutes.unshift(item);
  } else {
    userSearchedRoutes.unshift({
      code: cleanCode,
      desc: routeDesc || ('Route ' + cleanCode),
      count: 1,
      lastUsed: Date.now()
    });
  }
  userSearchedRoutes = userSearchedRoutes.slice(0, 10);
  saveLocal('transit.userSearchedRoutes', userSearchedRoutes);
  renderMostSearchedRoutes();
}

function renderMostSearchedRoutes() {
  const container = $('mostly-searched-routes');
  const clearBtn = $('clear-searched-routes');
  if (!container) return;
  container.replaceChildren();

  if (userSearchedRoutes && userSearchedRoutes.length > 0) {
    if (clearBtn) clearBtn.hidden = false;
    userSearchedRoutes.forEach(r => {
      const btn = element('button', '');
      btn.type = 'button';
      btn.className = 'searched-route-chip';
      btn.innerHTML = `<span class="chip-rt-badge">${r.code}</span><span class="chip-desc">${r.desc || r.code}</span>`;
      btn.onclick = () => {
        recordUserSearchedRoute(r.code, r.desc);
        const activeBus = vehicles.find(v => !v.stale && (v.route_id === r.code));
        openBusTracker(activeBus, r.code, r.code);
      };
      container.append(btn);
    });
  } else {
    if (clearBtn) clearBtn.hidden = true;
    const defaultTopRoutes = [
      { code: '729', desc: 'Mori Gate ⇄ Kapashera' },
      { code: '502', desc: 'Mehrauli ⇄ Kashmere Gate' },
      { code: '990', desc: 'Rohini ⇄ Shivaji Stadium' },
      { code: '419', desc: 'Ambedkar Ngr ⇄ Kashmere Gate' },
      { code: '73', desc: 'Tilak Nagar ⇄ Anand Vihar' }
    ];
    defaultTopRoutes.forEach(r => {
      const btn = element('button', '');
      btn.type = 'button';
      btn.className = 'searched-route-chip default';
      btn.innerHTML = `<span class="chip-rt-badge">${r.code}</span><span class="chip-desc">${r.desc}</span>`;
      btn.onclick = () => {
        recordUserSearchedRoute(r.code, r.desc);
        const activeBus = vehicles.find(v => !v.stale && (v.route_id === r.code));
        openBusTracker(activeBus, r.code, r.code);
      };
      container.append(btn);
    });
  }
}

const clearSearchedBtn = $('clear-searched-routes');
if (clearSearchedBtn) {
  clearSearchedBtn.onclick = () => {
    userSearchedRoutes = [];
    saveLocal('transit.userSearchedRoutes', []);
    renderMostSearchedRoutes();
  };
}
renderMostSearchedRoutes();

// Toggle Rush-Hour Crowding Hotspots
const hotspotsBtn = $('toggle-hotspots');
if (hotspotsBtn) {
  hotspotsBtn.onclick = async () => {
    hotspotsActive = !hotspotsActive;
    hotspotsBtn.classList.toggle('active', hotspotsActive);
    if (!hotspotsActive) {
      hotspots.clearLayers();
      safeSetText('map-headline', 'Delhi buses, live');
      safeSetText('map-description', 'Live buses moving across Delhi · updates every 30s');
      return;
    }
    safeSetText('map-headline', '🔥 Rush-Hour Crowding Hotspots');
    safeSetText('map-description', 'Loading verified peak bottleneck corridors across Delhi…');
    try {
      const d = await get('/crowding/top?limit=15&min_confidence=medium');
      hotspots.clearLayers();
      if (!d.items || !d.items.length) {
        safeSetText('map-description', 'No severe bottlenecks found for this filter.');
        return;
      }
      safeSetText('map-description', `Showing top ${d.items.length} rush-hour bottlenecks (standing & peak loads). Tap 🔥 pins for details.`);
      const pts = [];
      d.items.forEach((item, idx) => {
        if (item.stop_lat && item.stop_lon) {
          pts.push([item.stop_lat, item.stop_lon]);
          const flameHtml = `<div class="hotspot-flame-marker"><span class="flame-core">🔥</span><span class="flame-ring"></span></div>`;
          const marker = L.marker([item.stop_lat, item.stop_lon], {
            icon: L.divIcon({ className: 'hotspot-marker-wrap', html: flameHtml, iconSize: [36, 36], iconAnchor: [18, 18] }),
            title: `Hotspot #${idx + 1}: ${item.from_stop} (Route ${item.route_short})`
          });
          const timeSlot = item.half_hour !== undefined ? `${String(Math.floor(item.half_hour / 2)).padStart(2, '0')}:${item.half_hour % 2 ? '30' : '00'}` : 'Peak Hours';
          const popupContent = element('div', '');
          popupContent.className = 'hotspot-popup-card';
          popupContent.innerHTML = `
            <div class="hotspot-header"><span class="hotspot-rank">🔥 #${idx + 1} CROWDED</span><span class="hotspot-route">${item.route_short}</span></div>
            <h4>${item.from_stop || 'Transit Stretch'}</h4>
            <div class="hotspot-stats">
              <div><small>Estimated Load</small><strong>~${Math.round(item.load_est)} riders</strong></div>
              <div><small>Crowd Ratio</small><strong class="hotspot-ratio">${item.ratio}x</strong></div>
            </div>
            <div class="hotspot-meta"><span>🕒 Peak slot: ~${timeSlot}</span><span class="hotspot-level-pill ${item.level}">${item.level.toUpperCase()}</span></div>
          `;
          marker.bindPopup(popupContent).addTo(hotspots);
        }
      });
      if (pts.length) map.fitBounds(L.latLngBounds(pts), { padding: [40, 40] });
    } catch (err) {
      safeSetText('map-description', 'Hotspots unavailable: ' + err.message);
    }
  };
}

const preference = readSaved('transit.busType', 'all');
document.querySelectorAll('input[name=bus-type]').forEach(r => {
  r.checked = r.value === preference;
  r.onchange = () => {
    saveLocal('transit.busType', r.value);
    $('bus-type-note').textContent = r.value === 'all' ? 'Showing all buses. Bus type is unverified.' : 'Preference saved. AC / Non-AC filtering awaits verified vehicle data; all buses remain visible.';
  };
});
$('profile-button').onclick = () => { $('rider-name').value = readSaved('transit.name', ''); $('profile-dialog').showModal(); };
$('save-profile').onclick = () => { const name = $('rider-name').value.trim(); const saved = saveLocal('transit.name', name); $('profile-status').textContent = saved ? 'Profile saved on this device.' : 'Storage unavailable in this browser.'; if (saved) { $('profile-button').textContent = name ? name[0].toUpperCase() : '♙'; $('profile-dialog').close(); } };
const savedName = readSaved('transit.name', ''); if (typeof savedName === 'string' && savedName) $('profile-button').textContent = savedName[0].toUpperCase(); renderRecent();

// Modern Smart Journey Planner Card Binding
const planButton = $('find-bus-btn') || element('button', 'Find bus journeys');
const journeyOutput = $('real-journey-results') || element('section', '');
const priority = $('journey-priority') || { value: 'fastest' };
const departureInput = $('rider-departure');
if (departureInput && !departureInput.value) {
  departureInput.value = new Intl.DateTimeFormat('sv-SE', {
    timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23'
  }).format(new Date()).replace(' ', 'T');
}

planButton.onclick = async () => {
  const typedOrigin = $('pickup-search').value.trim();
  const origin = typedOrigin === 'Current location' ? (window.pickupCoordinates || '') : (typedOrigin === window.selectedPickupName ? window.selectedPickupCoordinates : typedOrigin);
  const typedDestination = $('destination-search').value.trim();
  const dest = typedDestination === window.destinationName ? window.destinationCoordinates : typedDestination;
  if (!origin || !dest || origin === 'Current location') {
    journeyOutput.textContent = 'Select a pickup stop and destination stop to plan your journey.';
    return;
  }
  planButton.disabled = true; journeyOutput.textContent = 'Finding smart bus journeys…';
  try {
    const priorityVal = $('journey-priority')?.value || priority?.value || 'fastest';
    const d = await get('/journeys?' + new URLSearchParams({ from: origin, to: dest, mode: priorityVal, ...($('rider-departure').value ? { depart: $('rider-departure').value } : {}) }));
    journeyOutput.replaceChildren();
    if (!d.options || !d.options.length) {
      const emptyCard = element('div', '');
      emptyCard.className = 'planner-no-results-card';
      emptyCard.innerHTML = `
        <div class="no-results-icon">🧭</div>
        <h3>Route suggestions via major transit hubs</h3>
        <p>No direct corridor found for this specific stop pair. Connect via Delhi's high-frequency transit hubs:</p>
        <div class="quick-hub-shortcuts">
          <button type="button" class="btn-hub-chip" onclick="$('destination-search').value='Kashmere Gate'; window.destinationCoordinates='28.6675,77.2285'; $('find-bus-btn').click();">🚏 Kashmere Gate ISBT</button>
          <button type="button" class="btn-hub-chip" onclick="$('destination-search').value='Anand Vihar'; window.destinationCoordinates='28.6469,77.3160'; $('find-bus-btn').click();">🚏 Anand Vihar ISBT</button>
          <button type="button" class="btn-hub-chip" onclick="$('destination-search').value='Rajiv Chowk'; window.destinationCoordinates='28.6328,77.2195'; $('find-bus-btn').click();">🚏 Rajiv Chowk / CP</button>
          <button type="button" class="btn-hub-chip" onclick="$('destination-search').value='Dhaula Kuan'; window.destinationCoordinates='28.5925,77.1610'; $('find-bus-btn').click();">🚏 Dhaula Kuan</button>
        </div>
        <button type="button" class="btn-retry-nearby" onclick="$('rider-departure').value=''; $('find-bus-btn').click();">Reset departure time & search again</button>
      `;
      journeyOutput.append(emptyCard);
      return;
    }
    d.options.forEach((o, idx) => {
      const isComfortable = o.crowding === 'seats';
      const isTop = idx === 0;
      let recBadgeText = null;
      if (isTop && isComfortable) {
        recBadgeText = '⚡ SMART RECOMMENDED · SEATS LIKELY';
      } else if (isTop) {
        recBadgeText = '⚡ RECOMMENDED · FASTEST ROUTE';
      } else if (isComfortable) {
        recBadgeText = '🛋️ HIGHEST SEAT PROBABILITY';
      }

      // Filter all active buses on this route corridor
      const cleanRoute = String(o.route).trim().toUpperCase();
      const activeRouteBuses = vehicles.filter(v =>
        !v.stale && (
          v.route_id === cleanRoute ||
          (v.route_id && String(v.route_id).toUpperCase() === cleanRoute) ||
          (cleanRoute.startsWith(String(v.route_id || '').toUpperCase()))
        )
      );

      // Sort active buses by proximity to the pickup stop
      let refLat = 28.6315, refLon = 77.2167;
      if (o.board && o.board.lat && o.board.lon) {
        refLat = o.board.lat;
        refLon = o.board.lon;
      } else if (window.selectedPickupCoordinates) {
        [refLat, refLon] = window.selectedPickupCoordinates.split(',').map(Number);
      }

      activeRouteBuses.sort((a, b) => {
        const da = haversineM(refLat, refLon, a.lat, a.lon);
        const db = haversineM(refLat, refLon, b.lat, b.lon);
        return da - db;
      });

      const nearestBus = activeRouteBuses.length > 0 ? activeRouteBuses[0] : null;
      let nearestDistM = nearestBus ? haversineM(refLat, refLon, nearestBus.lat, nearestBus.lon) : Infinity;

      const busCount = activeRouteBuses.length;
      let fleetPreviewHtml = '';
      if (busCount > 0 && nearestBus) {
        const distLabel = nearestDistM < 1000 ? Math.round(nearestDistM) + 'm' : (nearestDistM / 1000).toFixed(1) + ' km';
        const isAC = nearestBus.bus_type === 'ac';
        fleetPreviewHtml = `
          <div class="journey-fleet-banner">
            <div class="fleet-count-line">
              <span class="live-pulse-dot"></span>
              <strong>${busCount} active ${busCount === 1 ? 'bus' : 'buses'} running now</strong>
            </div>
            <div class="nearest-bus-line">
              <span class="nearest-star">⭐ Nearest:</span>
              <span class="nearest-bus-chip">${isAC ? '❄ ' : '🚌 '}Bus #${nearestBus.id}</span>
              <span class="nearest-dist">~${distLabel} from pickup</span>
            </div>
          </div>
        `;
      } else {
        fleetPreviewHtml = `
          <div class="journey-fleet-banner scheduled">
            <div class="fleet-count-line">
              <span class="sched-icon">🕒</span>
              <span>No live GPS bus broadcasting · Running on regular schedule</span>
            </div>
          </div>
        `;
      }

      // Estimate route distance for DTC fare
      let totalDistKm = 0;
      if (o.geometry && o.geometry.length > 1) {
        for (let i = 0; i < o.geometry.length - 1; i++) {
          totalDistKm += haversineM(o.geometry[i][0], o.geometry[i][1], o.geometry[i + 1][0], o.geometry[i + 1][1]) / 1000;
        }
      } else {
        totalDistKm = (o.stops || 12) * 0.75;
      }
      const fareObj = calculateDTCFare(totalDistKm, activeBusType);

      const card = element('article', ''); card.className = 'smart-journey-card';
      let html = '';
      if (recBadgeText) {
        html += `<div class="smart-rec-badge ${isComfortable ? 'seat-green' : 'speed-blue'}">${recBadgeText}</div>`;
      }
      const crowdLabel = o.crowding === 'seats' ? 'Seats likely' : (o.crowding === 'standing' ? 'Standing room' : (o.crowding === 'crowded' ? 'High crowding' : 'Crowding unobserved'));
      const crowdClass = o.crowding === 'crowded' ? 'alert-crowd' : (o.crowding === 'standing' ? 'standing' : (o.crowding === 'seats' ? 'seats' : 'unknown'));

      // Multi-leg breakdown HTML
      let legsHtml = '';
      if (o.legs && o.legs.length > 1) {
        legsHtml = `
          <div class="smart-legs-breakdown">
            ${o.legs.map((leg, lIdx) => `
              <div class="smart-leg-item">
                <div class="leg-step-badge">Leg ${lIdx + 1}</div>
                <div class="leg-step-info">
                  <div class="leg-step-route">🚌 Route <strong>${formatRoute(leg.route)}</strong> <span class="leg-time">${leg.ride_min} min ride</span></div>
                  <div class="leg-step-stops">${leg.board.name} ➔ ${leg.alight.name} (${leg.stops} stops)</div>
                </div>
              </div>
              ${lIdx < o.legs.length - 1 ? `
                <div class="leg-transfer-divider">
                  <span>🔁 Interchange at <strong>${o.legs[lIdx].alight.name}</strong> · ~${o.transfer_min || 3} min buffer</span>
                </div>
              ` : ''}
            `).join('')}
          </div>
        `;
      }

      const transferPillHtml = o.transfers
        ? `<span class="transfer-pill ${o.transfers > 1 ? 'multi' : ''}">${o.transfers} transfer${o.transfers > 1 ? 's' : ''}</span>`
        : `<span class="direct-pill">Direct Bus</span>`;

      html += `
        <div class="smart-journey-header">
          <div class="route-badge-wrap">
            <span class="smart-route-badge">${formatRoute(o.route)}</span>
            ${transferPillHtml}
          </div>
          <div class="smart-duration">
            <span class="num">${o.total_min}</span><small>min</small>
          </div>
        </div>
        <div class="smart-journey-stops">
          <span class="stop-dot start"></span>
          <span class="stop-name origin">${o.board.name}</span>
          <span class="stop-arrow">→</span>
          <span class="stop-dot end"></span>
          <span class="stop-name dest">${o.alight.name}</span>
        </div>
        <div class="smart-chips-row">
          <span class="smart-chip">🚶 ${o.walk_min}m walk</span>
          <span class="smart-chip">⏳ ~${o.wait_min}m wait</span>
          <span class="smart-chip">🚌 ${o.ride_min}m ride</span>
          <span class="smart-crowd-chip ${crowdClass}">● ${crowdLabel}</span>
        </div>
        ${o.transfers && o.transfer_stop && (!o.legs || o.legs.length <= 1) ? `<div class="smart-transfer-info">🔁 Change bus at <strong>${o.transfer_stop}</strong> · ~${o.transfer_min} min buffer</div>` : ''}
        ${legsHtml}
        ${fleetPreviewHtml}
        <div class="journey-fare-row">
          <div class="fare-pills-wrap">
            <span class="fare-cost-tag">🎫 ₹${fareObj.standard} (Non-AC) · ❄ ₹${fareObj.ac} (AC)</span>
            <span class="pink-ticket-tag">🌸 Free Pink Pass (Women)</span>
          </div>
        </div>
        <div class="journey-card-actions-single">
          <button type="button" class="btn-view-map-link">🗺️ View Complete Route on Map</button>
        </div>
      `;

      card.innerHTML = html;

      const showRouteOnMap = (e) => {
        if (e) e.stopPropagation();
        paths.clearLayers();

        // 1. Draw True Road Polyline
        const legs = o.legs || [o];
        legs.forEach((leg, i) => {
          if (leg.geometry && leg.geometry.length) {
            // Road casing
            L.polyline(leg.geometry, {
              color: '#064e3b',
              weight: 7.5,
              opacity: 0.8,
              lineJoin: 'round',
              lineCap: 'round'
            }).addTo(paths);

            // Vibrant road line
            L.polyline(leg.geometry, {
              color: i ? '#0284c7' : '#10b981',
              weight: 4.5,
              opacity: 0.95,
              lineJoin: 'round',
              lineCap: 'round'
            }).addTo(paths);
          }
        });

        // 2. Intermediate Stations (along the road between start and end)
        const stations = o.stations || [];
        if (stations.length > 0) {
          stations.forEach((st, sIdx) => {
            // Skip the first and last stops as they receive prominent Location Pointer Pins
            if (sIdx === 0 || sIdx === stations.length - 1) return;

            const stopSeq = st.seq !== undefined ? st.seq : (sIdx + 1);
            const stationIcon = L.divIcon({
              className: 'map-station-stop-wrap',
              html: `
                <div class="map-station-marker" title="Bus Stand ${stopSeq}: ${st.name}">
                  <span class="station-marker-icon">🚏</span>
                  <span class="station-seq-num">${stopSeq}</span>
                </div>
              `,
              iconSize: [28, 28],
              iconAnchor: [14, 14]
            });

            const stMarker = L.marker([st.lat, st.lon], { icon: stationIcon, zIndexOffset: 500 });
            stMarker.bindTooltip(`<strong>🚏 Stop ${stopSeq}:</strong> ${st.name}`, {
              permanent: false,
              direction: 'top',
              offset: [0, -14],
              className: 'station-hover-tooltip'
            });

            stMarker.bindPopup(`
              <div class="map-station-popup">
                <span class="station-seq-badge">🚏 Bus Stand ${stopSeq} of ${stations.length}</span>
                <strong>${st.name}</strong>
                <p class="station-route-meta">Route ${o.route} · Bus Stand</p>
              </div>
            `);

            stMarker.addTo(paths);
          });
        }

        // 3. Location Pointer Logo at START (Pickup Stop)
        if (o.board && o.board.lat && o.board.lon) {
          const startPin = L.divIcon({
            className: 'journey-pointer-wrap',
            html: `
              <div class="route-location-pin start" title="Start / Pickup Stop: ${o.board.name}">
                <div class="pin-marker-body">
                  <span class="pin-logo">📍</span>
                  <span class="pin-label">START: ${o.board.name}</span>
                </div>
                <div class="pin-pointer-tip"></div>
                <div class="pin-pulse-ring"></div>
              </div>
            `,
            iconSize: [74, 44],
            iconAnchor: [37, 42]
          });

          L.marker([o.board.lat, o.board.lon], { icon: startPin, zIndexOffset: 2000 })
            .bindPopup(`
              <div class="map-station-popup">
                <span class="station-seq-badge" style="background:#dcfce7;color:#15803d;">📍 START / PICKUP</span>
                <strong>${o.board.name}</strong>
                <p class="station-route-meta">Board Route ${o.route}</p>
              </div>
            `)
            .addTo(paths);
        }

        // 4. Location Pointer Logo at END (Destination Stop)
        if (o.alight && o.alight.lat && o.alight.lon) {
          const endPin = L.divIcon({
            className: 'journey-pointer-wrap',
            html: `
              <div class="route-location-pin end" title="Destination Stop: ${o.alight.name}">
                <div class="pin-marker-body">
                  <span class="pin-logo">🏁</span>
                  <span class="pin-label">DESTINATION: ${o.alight.name}</span>
                </div>
                <div class="pin-pointer-tip"></div>
              </div>
            `,
            iconSize: [104, 44],
            iconAnchor: [52, 42]
          });

          L.marker([o.alight.lat, o.alight.lon], { icon: endPin, zIndexOffset: 2000 })
            .bindPopup(`
              <div class="map-station-popup">
                <span class="station-seq-badge" style="background:#fee2e2;color:#b91c1c;">🏁 DESTINATION</span>
                <strong>${o.alight.name}</strong>
                <p class="station-route-meta">Alight here</p>
              </div>
            `)
            .addTo(paths);
        }

        // 5. Fit bounds to full road path
        if (o.geometry && o.geometry.length) {
          map.fitBounds(L.polyline(o.geometry).getBounds(), { padding: [50, 50] });
        }

        document.querySelector('.app').classList.add('journey-map');
        if (typeof showScreen === 'function') showScreen('map');
        map.invalidateSize();
        safeSetText('map-headline', 'Route ' + o.route);
        safeSetText('map-description', `${o.board.name} → ${o.alight.name} · ${stations.length || o.stops} stops along corridor`);
      };

      const viewMapBtn = card.querySelector('.btn-view-map-link');
      if (viewMapBtn) viewMapBtn.onclick = showRouteOnMap;
      showRouteOnMapGlobal = showRouteOnMap;
      card.onclick = showRouteOnMap;
      journeyOutput.append(card);
    });
  } catch (e) {
    journeyOutput.textContent = e.message;
  } finally {
    planButton.disabled = false;
  }
};

const backToOptions = element('button', 'Back to journey options'); backToOptions.id = 'back-to-options'; backToOptions.onclick = () => document.querySelector('.app').classList.remove('journey-map'); document.querySelector('.map-panel').append(backToOptions);
const noteEl = $('bus-type-note');
const typeMessage = () => { if (noteEl) noteEl.textContent = activeBusType === 'all' ? 'Showing all live buses. AC type, when known, is inferred from October tickets.' : 'Map filter: ' + (activeBusType === 'ac' ? 'AC' : 'Non-AC') + ' from consistent October ticket evidence. Unknown vehicles are hidden; journey options are not filtered.'; };
activeBusType = ['ac', 'non-ac'].includes(preference) ? preference : 'all';
document.querySelectorAll('input[name=bus-type]').forEach(r => { r.onchange = () => { activeBusType = r.value; saveLocal('transit.busType', r.value); typeMessage(); paint(); }; }); typeMessage();
