/**
 * Custos Push Bildirim Yönetimi — Alpine.js component.
 *
 * Settings sayfasında kullanılır. Service Worker kaydı,
 * push subscription oluşturma/silme işlemlerini yönetir.
 */

document.addEventListener("alpine:init", function () {
  Alpine.data("pushManager", function () {
    return {
      supported: "serviceWorker" in navigator && "PushManager" in window,
      subscribed: false,
      loading: false,
      error: "",
      vapidPublicKey: "",

      async init() {
        if (!this.supported) return;

        // VAPID public key'i backend'den al
        try {
          const resp = await fetch("/api/push/vapid-public-key");
          if (!resp.ok) return;
          const data = await resp.json();
          this.vapidPublicKey = data.public_key;
          if (!this.vapidPublicKey) return;
        } catch (_e) {
          return;
        }

        // Mevcut subscription kontrolü
        try {
          const reg = await navigator.serviceWorker.getRegistration("/static/js/");
          if (reg) {
            const sub = await reg.pushManager.getSubscription();
            this.subscribed = !!sub;
          }
        } catch (_e) {
          // Service Worker henüz kayıtlı değil
        }
      },

      async subscribe() {
        if (!this.supported || !this.vapidPublicKey) return;
        this.loading = true;
        this.error = "";

        try {
          // Service Worker kaydet
          const reg = await navigator.serviceWorker.register("/static/js/sw.js");
          await navigator.serviceWorker.ready;

          // applicationServerKey'i Uint8Array'e çevir
          const key = this._urlBase64ToUint8Array(this.vapidPublicKey);

          // Push subscription oluştur
          const sub = await reg.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: key,
          });

          // Backend'e kaydet
          const subJson = sub.toJSON();
          const resp = await fetch("/api/push/subscribe", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              endpoint: subJson.endpoint,
              p256dh: subJson.keys.p256dh,
              auth: subJson.keys.auth,
            }),
          });

          if (!resp.ok) {
            throw new Error("Abonelik kaydedilemedi");
          }

          this.subscribed = true;
        } catch (e) {
          this.error = e.message || "Abonelik başarısız";
        } finally {
          this.loading = false;
        }
      },

      async unsubscribe() {
        this.loading = true;
        this.error = "";

        try {
          const reg = await navigator.serviceWorker.getRegistration("/static/js/");
          if (reg) {
            const sub = await reg.pushManager.getSubscription();
            if (sub) {
              // Backend'den sil
              await fetch("/api/push/subscribe", {
                method: "DELETE",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ endpoint: sub.endpoint }),
              });

              // Tarayıcıdan iptal et
              await sub.unsubscribe();
            }
          }
          this.subscribed = false;
        } catch (e) {
          this.error = e.message || "İptal başarısız";
        } finally {
          this.loading = false;
        }
      },

      async sendTest() {
        this.error = "";
        try {
          const resp = await fetch("/api/push/test", { method: "POST" });
          if (!resp.ok) {
            const data = await resp.json();
            this.error = data.detail || "Test bildirimi gönderilemedi";
          }
        } catch (e) {
          this.error = e.message || "Test bildirimi gönderilemedi";
        }
      },

      _urlBase64ToUint8Array(base64String) {
        const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
        const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
        const rawData = window.atob(base64);
        const outputArray = new Uint8Array(rawData.length);
        for (let i = 0; i < rawData.length; i++) {
          outputArray[i] = rawData.charCodeAt(i);
        }
        return outputArray;
      },
    };
  });
});
