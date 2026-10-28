"use strict";

const state = {
  user: null,
  csrfToken: null,
  movies: [],
  screenings: [],
  halls: [],
  selectedScreening: null,
  selectedSeats: new Set(),
  seatData: null,
  authMode: "login",
};

const $ = (selector) => document.querySelector(selector);
const CINEMA_TIME_ZONE = "Europe/Moscow";

function showToast(message, isError = false) {
  const toast = document.createElement("div");
  toast.className = `toast${isError ? " is-error" : ""}`;
  toast.textContent = message;
  $("#toast-region").append(toast);
  window.setTimeout(() => toast.remove(), 4800);
}

function formatMoney(cents) {
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 0,
  }).format(cents / 100);
}

function formatDateTime(value) {
  return new Intl.DateTimeFormat("ru-RU", {
    timeZone: CINEMA_TIME_ZONE,
    day: "numeric",
    month: "long",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatTime(value) {
  return new Intl.DateTimeFormat("ru-RU", {
    timeZone: CINEMA_TIME_ZONE,
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  let body = options.body;
  if (body && typeof body !== "string") {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(body);
  }
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && state.csrfToken) {
    headers.set("X-CSRF-Token", state.csrfToken);
  }
  const response = await fetch(path, {
    ...options,
    method,
    body,
    headers,
    credentials: "same-origin",
  });
  const payload = response.status === 204 ? {} : await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error?.message || `Ошибка ${response.status}`);
    error.code = payload.error?.code;
    error.details = payload.error?.details;
    if (response.status === 401) {
      state.user = null;
      state.csrfToken = null;
      renderAccount();
    }
    throw error;
  }
  return payload;
}

function setDefaultDate() {
  const tomorrow = new Date(Date.now() + 24 * 60 * 60 * 1000);
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: CINEMA_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(tomorrow);
  const value = (type) => parts.find((part) => part.type === type).value;
  $("#date-filter").value = `${value("year")}-${value("month")}-${value("day")}`;
}

async function loadSession() {
  const result = await api("/api/me");
  state.user = result.user;
  state.csrfToken = result.csrf_token;
  renderAccount();
}

function renderAccount() {
  const loggedIn = Boolean(state.user);
  $("#login-button").classList.toggle("is-hidden", loggedIn);
  $("#logout-button").classList.toggle("is-hidden", !loggedIn);
  $("#bookings-link").classList.toggle("is-hidden", !loggedIn);
  $("#bookings").classList.toggle("is-hidden", !loggedIn);
  const isAdmin = state.user?.role === "admin";
  $("#admin-link").classList.toggle("is-hidden", !isAdmin);
  $("#admin").classList.toggle("is-hidden", !isAdmin);
  $("#account-label").textContent = state.user?.email || "Гость";
  if (loggedIn) {
    loadBookings().catch(handleError);
  } else {
    $("#booking-list").replaceChildren();
  }
  if (isAdmin) {
    loadHalls().catch(handleError);
  }
}

async function loadCatalog() {
  const movieResult = await api("/api/movies");
  state.movies = movieResult.movies;
  fillMovieSelects();
  await loadScreenings();
}

async function loadScreenings() {
  const params = new URLSearchParams();
  const date = $("#date-filter").value;
  const movieId = $("#movie-filter").value;
  if (date) params.set("date", date);
  if (movieId) params.set("movie_id", movieId);
  const result = await api(`/api/screenings?${params.toString()}`);
  state.screenings = result.screenings;
  renderMovies();
  fillScreeningSelect();
}

function fillMovieSelects() {
  const publicSelect = $("#movie-filter");
  const current = publicSelect.value;
  publicSelect.replaceChildren(new Option("Все фильмы", ""));
  const adminSelect = $("#admin-movie-select");
  adminSelect.replaceChildren();
  for (const movie of state.movies) {
    publicSelect.add(new Option(movie.title, String(movie.id)));
    adminSelect.add(new Option(movie.title, String(movie.id)));
  }
  publicSelect.value = current;
}

function renderMovies() {
  const container = $("#movie-grid");
  container.replaceChildren();
  const screeningsByMovie = new Map();
  for (const screening of state.screenings) {
    const list = screeningsByMovie.get(screening.movie_id) || [];
    list.push(screening);
    screeningsByMovie.set(screening.movie_id, list);
  }
  const visibleMovieIds = new Set(state.screenings.map((item) => item.movie_id));
  const selectedMovie = Number($("#movie-filter").value || 0);
  const movies = state.movies.filter((movie) =>
    selectedMovie ? movie.id === selectedMovie : visibleMovieIds.has(movie.id)
  );
  if (!movies.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "На выбранную дату сеансов нет. Попробуйте другой день.";
    container.append(empty);
    return;
  }
  for (const movie of movies) {
    const card = document.createElement("article");
    card.className = "movie-card";
    card.dataset.initial = movie.title.slice(0, 1).toUpperCase();

    const meta = document.createElement("div");
    meta.className = "movie-meta";
    meta.textContent = `${movie.age_rating} · ${movie.duration_min} мин`;
    const title = document.createElement("h3");
    title.textContent = movie.title;
    const description = document.createElement("p");
    description.className = "movie-description";
    description.textContent = movie.description;
    const sessions = document.createElement("div");
    sessions.className = "session-list";

    for (const screening of screeningsByMovie.get(movie.id) || []) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "session-button";
      button.addEventListener("click", () => openSeatMap(screening.id));
      const time = document.createElement("span");
      time.className = "session-time";
      time.textContent = formatTime(screening.starts_at);
      const detail = document.createElement("span");
      detail.className = "session-detail";
      detail.textContent = `${screening.hall_name}${screening.description ? ` · ${screening.description}` : ""}`;
      const price = document.createElement("span");
      price.className = "session-price";
      price.textContent = formatMoney(screening.base_price_cents);
      button.append(time, detail, price);
      sessions.append(button);
    }
    card.append(meta, title, description, sessions);
    container.append(card);
  }
}

async function openSeatMap(screeningId) {
  const result = await api(`/api/screenings/${screeningId}/seats`);
  state.seatData = result;
  state.selectedScreening = result.screening;
  state.selectedSeats.clear();
  $("#seat-title").textContent = result.screening.movie_title;
  $("#seat-meta").textContent = `${formatDateTime(result.screening.starts_at)} · ${result.screening.hall_name}`;
  renderSeatMap();
  $("#seat-dialog").showModal();
}

function renderSeatMap() {
  const map = $("#seat-map");
  map.replaceChildren();
  const rows = new Map();
  for (const seat of state.seatData.seats) {
    const row = rows.get(seat.row_number) || [];
    row.push(seat);
    rows.set(seat.row_number, row);
  }
  for (const [rowNumber, seats] of rows.entries()) {
    const row = document.createElement("div");
    row.className = "seat-row-map";
    const label = document.createElement("span");
    label.className = "row-label";
    label.textContent = `Р${rowNumber}`;
    row.append(label);
    for (const seat of seats) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "seat";
      button.textContent = String(seat.seat_number);
      button.disabled = !seat.available;
      button.setAttribute("aria-label", `Ряд ${seat.row_number}, место ${seat.seat_number}${seat.available ? "" : ", занято"}`);
      button.addEventListener("click", () => {
        if (state.selectedSeats.has(seat.id)) {
          state.selectedSeats.delete(seat.id);
          button.classList.remove("is-selected");
        } else if (state.selectedSeats.size < 10) {
          state.selectedSeats.add(seat.id);
          button.classList.add("is-selected");
        } else {
          showToast("За один раз можно выбрать не более 10 мест", true);
        }
        updateSeatSummary();
      });
      row.append(button);
    }
    map.append(row);
  }
  updateSeatSummary();
}

function updateSeatSummary() {
  const selected = state.seatData.seats.filter((seat) => state.selectedSeats.has(seat.id));
  $("#selected-label").textContent = selected.length
    ? selected.map((seat) => `${seat.row_number} ряд, ${seat.seat_number} место`).join("; ")
    : "Места не выбраны";
  $("#selected-total").textContent = formatMoney(
    selected.length * state.selectedScreening.base_price_cents
  );
  $("#reserve-button").disabled = selected.length === 0;
  $("#purchase-button").disabled = selected.length === 0;
}

async function submitBooking(mode) {
  if (!state.user) {
    $("#seat-dialog").close();
    openAuth("login");
    showToast("Войдите, чтобы оформить места");
    return;
  }
  const payload = {
    screening_id: state.selectedScreening.id,
    seat_ids: [...state.selectedSeats],
    mode,
  };
  const result = await api("/api/bookings", { method: "POST", body: payload });
  $("#seat-dialog").close();
  showToast(mode === "reserve" ? `Бронь ${result.booking.code} создана` : `Билеты ${result.booking.code} куплены`);
  await Promise.all([loadBookings(), loadScreenings()]);
  location.hash = "#bookings";
}

async function loadBookings() {
  if (!state.user) return;
  const result = await api("/api/bookings");
  const container = $("#booking-list");
  container.replaceChildren();
  if (!result.bookings.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "У вас пока нет бронирований или покупок.";
    container.append(empty);
    return;
  }
  const labels = {
    reserved: "Забронировано",
    paid: "Оплачено",
    cancelled: "Отменено",
    expired: "Срок истёк",
  };
  for (const booking of result.bookings) {
    const card = document.createElement("article");
    card.className = "booking-card";
    const info = document.createElement("div");
    const title = document.createElement("h3");
    title.textContent = booking.movie_title;
    const meta = document.createElement("div");
    meta.className = "booking-meta";
    const seats = booking.seats.map((seat) => `${seat.row_number} ряд, ${seat.seat_number} место`).join("; ");
    meta.textContent = `${formatDateTime(booking.starts_at)} · ${booking.hall_name} · ${seats}`;
    const code = document.createElement("div");
    code.className = "booking-code";
    code.textContent = `${booking.code} · ${formatMoney(booking.total_cents)}`;
    info.append(title, meta, code);
    const status = document.createElement("span");
    status.className = `status status-${booking.status}`;
    status.textContent = labels[booking.status] || booking.status;
    card.append(info, status);
    if (booking.status === "reserved") {
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.className = "button button-ghost";
      cancel.textContent = "Отменить бронь";
      cancel.addEventListener("click", async () => {
        try {
          await api(`/api/bookings/${booking.id}`, { method: "DELETE" });
          showToast("Бронь отменена");
          await Promise.all([loadBookings(), loadScreenings()]);
        } catch (error) {
          handleError(error);
        }
      });
      card.append(cancel);
    }
    container.append(card);
  }
}

function openAuth(mode) {
  state.authMode = mode;
  const isRegister = mode === "register";
  $("#auth-title").textContent = isRegister ? "Регистрация" : "Вход";
  $("#auth-submit").textContent = isRegister ? "Создать аккаунт" : "Войти";
  $("#password-hint").classList.toggle("is-hidden", !isRegister);
  $("#login-tab").classList.toggle("is-active", !isRegister);
  $("#register-tab").classList.toggle("is-active", isRegister);
  const passwordInput = $("#auth-form [name='password']");
  passwordInput.autocomplete = isRegister ? "new-password" : "current-password";
  if (!$("#auth-dialog").open) $("#auth-dialog").showModal();
}

async function submitAuth(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  const endpoint = state.authMode === "register" ? "register" : "login";
  try {
    const result = await api(`/api/auth/${endpoint}`, {
      method: "POST",
      body: { email: form.get("email"), password: form.get("password") },
    });
    state.user = result.user;
    state.csrfToken = result.csrf_token;
    $("#auth-dialog").close();
    formElement.reset();
    renderAccount();
    showToast(state.authMode === "register" ? "Аккаунт создан" : "Вход выполнен");
  } catch (error) {
    handleError(error);
  }
}

async function logout() {
  try {
    await api("/api/auth/logout", { method: "POST", body: {} });
    state.user = null;
    state.csrfToken = null;
    renderAccount();
    showToast("Вы вышли из аккаунта");
  } catch (error) {
    handleError(error);
  }
}

async function loadHalls() {
  if (state.user?.role !== "admin") return;
  const result = await api("/api/admin/halls");
  state.halls = result.halls;
  const select = $("#admin-hall-select");
  select.replaceChildren();
  for (const hall of state.halls.filter((item) => item.active)) {
    select.add(new Option(`${hall.name} · ${hall.capacity} мест`, String(hall.id)));
  }
  const list = $("#hall-list");
  list.replaceChildren();
  for (const hall of state.halls) {
    const row = document.createElement("div");
    row.className = "compact-item";
    const label = document.createElement("span");
    label.textContent = `${hall.name} · ${hall.rows_count}×${hall.seats_per_row}`;
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "button button-ghost";
    toggle.textContent = hall.active ? "Отключить" : "Включить";
    toggle.addEventListener("click", async () => {
      try {
        await api(`/api/admin/halls/${hall.id}`, {
          method: "PATCH",
          body: { active: !Boolean(hall.active) },
        });
        showToast("Статус зала изменён");
        await Promise.all([loadHalls(), loadScreenings()]);
      } catch (error) {
        handleError(error);
      }
    });
    row.append(label, toggle);
    list.append(row);
  }
}

function fillScreeningSelect() {
  const select = $("#admin-screening-select");
  select.replaceChildren();
  for (const screening of state.screenings) {
    select.add(new Option(
      `${screening.movie_title} · ${formatDateTime(screening.starts_at)} · ${screening.hall_name}`,
      String(screening.id)
    ));
  }
}

async function submitMovie(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const data = Object.fromEntries(new FormData(formElement));
  data.duration_min = Number(data.duration_min);
  try {
    await api("/api/admin/movies", { method: "POST", body: data });
    formElement.reset();
    showToast("Фильм добавлен");
    await loadCatalog();
  } catch (error) { handleError(error); }
}

async function submitHall(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const data = Object.fromEntries(new FormData(formElement));
  data.rows_count = Number(data.rows_count);
  data.seats_per_row = Number(data.seats_per_row);
  try {
    await api("/api/admin/halls", { method: "POST", body: data });
    formElement.reset();
    showToast("Зал создан");
    await loadHalls();
  } catch (error) { handleError(error); }
}

async function submitScreening(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const data = Object.fromEntries(new FormData(formElement));
  data.movie_id = Number(data.movie_id);
  data.hall_id = Number(data.hall_id);
  data.base_price_cents = Math.round(Number(data.price_rub) * 100);
  delete data.price_rub;
  try {
    const startsAt = new Date(`${data.starts_at}+03:00`);
    if (Number.isNaN(startsAt.getTime())) {
      throw new Error("Укажите корректные дату и время сеанса");
    }
    data.starts_at = startsAt.toISOString();
    await api("/api/admin/screenings", { method: "POST", body: data });
    formElement.reset();
    showToast("Сеанс создан");
    await loadScreenings();
  } catch (error) { handleError(error); }
}

async function submitPrice(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const data = Object.fromEntries(new FormData(formElement));
  try {
    await api(`/api/admin/screenings/${Number(data.screening_id)}/price`, {
      method: "PATCH",
      body: { base_price_cents: Math.round(Number(data.price_rub) * 100) },
    });
    formElement.reset();
    showToast("Цена изменена");
    await loadScreenings();
  } catch (error) { handleError(error); }
}

function handleError(error) {
  console.error(error);
  showToast(error.message || "Не удалось выполнить действие", true);
}

async function init() {
  setDefaultDate();
  $("#login-button").addEventListener("click", () => openAuth("login"));
  $("#logout-button").addEventListener("click", logout);
  $("#login-tab").addEventListener("click", () => openAuth("login"));
  $("#register-tab").addEventListener("click", () => openAuth("register"));
  $("#auth-form").addEventListener("submit", submitAuth);
  $("#schedule-filter").addEventListener("submit", (event) => {
    event.preventDefault();
    loadScreenings().catch(handleError);
  });
  $("#reserve-button").addEventListener("click", () => submitBooking("reserve").catch(handleError));
  $("#purchase-button").addEventListener("click", () => submitBooking("purchase").catch(handleError));
  $("#refresh-bookings").addEventListener("click", () => loadBookings().catch(handleError));
  $("#movie-form").addEventListener("submit", submitMovie);
  $("#hall-form").addEventListener("submit", submitHall);
  $("#screening-form").addEventListener("submit", submitScreening);
  $("#price-form").addEventListener("submit", submitPrice);
  try {
    await loadSession();
    await loadCatalog();
  } catch (error) {
    handleError(error);
  }
}

document.addEventListener("DOMContentLoaded", init);
