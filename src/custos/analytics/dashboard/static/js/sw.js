/* Custos Service Worker — Web Push bildirim alıcısı. */

self.addEventListener("push", function (event) {
  if (!event.data) return;

  let payload;
  try {
    payload = event.data.json();
  } catch (_e) {
    payload = { title: "Custos", body: event.data.text() };
  }

  const title = payload.title || "Custos Alarm";
  const options = {
    body: payload.body || "",
    icon: "/static/img/custos-icon-192.png",
    badge: "/static/img/custos-icon-192.png",
    tag: payload.tag || "custos-alarm",
    data: { url: payload.url || "/dashboard/alarms" },
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  const url = event.notification.data && event.notification.data.url
    ? event.notification.data.url
    : "/dashboard/alarms";

  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (windowClients) {
      for (const client of windowClients) {
        if (client.url.includes("/dashboard") && "focus" in client) {
          return client.focus();
        }
      }
      return clients.openWindow(url);
    })
  );
});
