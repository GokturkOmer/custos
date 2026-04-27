/**
 * Custos Push Bildirim Yönetimi — Alpine.js component'leri.
 *
 * - ``pushManager``    : Bu tarayıcı için subscribe/unsubscribe (P-03 ile
 *                        label prompt eklendi).
 * - ``pushRecipients`` : Tüm aboneliklerin listesi + per-subscription edit
 *                        (label, enabled, severity tier'ları), test bildirimi,
 *                        master switch (Developer-only).
 *
 * Yetki: backend ``_can_modify_subscription`` ile zorlar (Operator kendi
 * aboneliği, Developer hepsi). UI tarafında ``canEdit`` flag'i ile
 * read-only / editable ayrımı yapılır.
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

        try {
          const resp = await fetch("/api/push/vapid-public-key");
          if (!resp.ok) return;
          const data = await resp.json();
          this.vapidPublicKey = data.public_key;
          if (!this.vapidPublicKey) return;
        } catch (_e) {
          return;
        }

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

        // P-03: Subscribe öncesi label sor — boş bırakılırsa tarayıcı/cihaz
        // adından tahmin edilen default. İptal edilirse abonelik iptal.
        const defaultLabel = this._defaultLabel();
        const label = window.prompt(
          "Bu cihazı tanımlamak için bir ad gir (örn. 'Ali — Telefon'):",
          defaultLabel,
        );
        if (label === null) {
          // Kullanıcı iptal etti — abonelik akışı baştan iptal.
          return;
        }

        this.loading = true;
        this.error = "";

        try {
          const reg = await navigator.serviceWorker.register("/static/js/sw.js");
          await navigator.serviceWorker.ready;

          const key = this._urlBase64ToUint8Array(this.vapidPublicKey);

          const sub = await reg.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: key,
          });

          const subJson = sub.toJSON();
          const resp = await fetch("/api/push/subscribe", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              endpoint: subJson.endpoint,
              p256dh: subJson.keys.p256dh,
              auth: subJson.keys.auth,
              label: label.trim(),
            }),
          });

          if (!resp.ok) {
            throw new Error("Abonelik kaydedilemedi");
          }

          this.subscribed = true;
          // Recipients listesi varsa yenile.
          window.dispatchEvent(new CustomEvent("custos:push-subs-changed"));
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
              await fetch("/api/push/subscribe", {
                method: "DELETE",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ endpoint: sub.endpoint }),
              });

              await sub.unsubscribe();
            }
          }
          this.subscribed = false;
          window.dispatchEvent(new CustomEvent("custos:push-subs-changed"));
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

      _defaultLabel() {
        // Cihaz tahmini — UA'dan platform çıkar, kısa form öner.
        const ua = navigator.userAgent || "";
        if (/Mobile|Android|iPhone|iPad/.test(ua)) return "Telefon";
        if (/Mac/.test(ua)) return "Mac";
        if (/Win/.test(ua)) return "Bilgisayar";
        return "";
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

  // P-03 Bildirim Alıcıları — Settings altındaki tablo + master switch.
  // currentUserId / isDeveloper template'ten parametrelenir.
  Alpine.data("pushRecipients", function (currentUserId, isDeveloper) {
    return {
      subs: [],
      loading: false,
      error: "",
      success: "",
      masterEnabled: true,
      currentUserId: currentUserId,
      isDeveloper: !!isDeveloper,

      async init() {
        await this.refresh();
        // Subscribe/unsubscribe sonrası listeyi tazele.
        window.addEventListener("custos:push-subs-changed", () => this.refresh());
      },

      async refresh() {
        this.loading = true;
        this.error = "";
        try {
          const [subsResp, switchResp] = await Promise.all([
            fetch("/api/push/subscriptions"),
            fetch("/api/push/master-switch"),
          ]);
          if (!subsResp.ok) throw new Error("Liste alınamadı");
          const subsData = await subsResp.json();
          this.subs = subsData.subscriptions || [];
          if (switchResp.ok) {
            const swData = await switchResp.json();
            this.masterEnabled = swData.push_global_enabled;
          }
        } catch (e) {
          this.error = e.message || "Liste alınamadı";
        } finally {
          this.loading = false;
        }
      },

      canEdit(sub) {
        // Backend de zorlar; UI burada sadece input disable için.
        if (this.isDeveloper) return true;
        return sub.created_by_user_id === this.currentUserId;
      },

      async updateField(sub, field, value) {
        if (!this.canEdit(sub)) return;
        this.error = "";
        this.success = "";
        const body = { endpoint: sub.endpoint, [field]: value };
        try {
          const resp = await fetch("/api/push/subscriptions", {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
          if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            this.error = data.detail || "Güncelleme başarısız";
            await this.refresh();
            return;
          }
          // Local state'i optimistik güncelle (refresh maliyetli olmasın).
          const idx = this.subs.findIndex((s) => s.endpoint === sub.endpoint);
          if (idx >= 0) this.subs[idx][field] = value;
          this.success = "Kaydedildi";
          setTimeout(() => (this.success = ""), 1500);
        } catch (e) {
          this.error = e.message || "Güncelleme başarısız";
        }
      },

      async deleteSub(sub) {
        if (!this.canEdit(sub)) return;
        const labelTxt = sub.label || "(etiketsiz)";
        if (!window.confirm(`'${labelTxt}' aboneliğini sil?`)) return;
        this.error = "";
        try {
          const resp = await fetch("/api/push/subscribe", {
            method: "DELETE",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ endpoint: sub.endpoint }),
          });
          if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            this.error = data.detail || "Silme başarısız";
            return;
          }
          this.subs = this.subs.filter((s) => s.endpoint !== sub.endpoint);
        } catch (e) {
          this.error = e.message || "Silme başarısız";
        }
      },

      async testSub(sub) {
        if (!this.canEdit(sub)) return;
        this.error = "";
        this.success = "";
        try {
          const resp = await fetch("/api/push/test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ endpoint: sub.endpoint }),
          });
          if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            this.error = data.detail || "Test gönderilemedi";
            return;
          }
          const data = await resp.json();
          if (data.sent === 0) {
            this.error = "Test gönderilemedi (cihaz ulaşılamaz olabilir).";
          } else {
            this.success = "Test bildirimi gönderildi";
            setTimeout(() => (this.success = ""), 2000);
          }
        } catch (e) {
          this.error = e.message || "Test gönderilemedi";
        }
      },

      async toggleMaster(newState) {
        if (!this.isDeveloper) return;
        this.error = "";
        try {
          const resp = await fetch("/api/push/master-switch", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled: newState }),
          });
          if (!resp.ok) {
            const data = await resp.json().catch(() => ({}));
            this.error = data.detail || "Master switch hatası";
            return;
          }
          const data = await resp.json();
          this.masterEnabled = data.push_global_enabled;
        } catch (e) {
          this.error = e.message || "Master switch hatası";
        }
      },
    };
  });
});
