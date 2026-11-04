/* Talks to the REST API. The sign-in token is kept in memory only: refreshing the page
 * or signing out forgets it, and it is never written to browser storage or a cookie. */
(function () {
  "use strict";

  var token = null;

  function ApiError(status, code, message) {
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.message = message;
  }
  ApiError.prototype = Object.create(Error.prototype);

  function request(method, path, body, extraHeaders) {
    var headers = { Accept: "application/json" };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
    }
    if (token) {
      headers.Authorization = "Bearer " + token;
    }
    Object.keys(extraHeaders || {}).forEach(function (name) {
      headers[name] = extraHeaders[name];
    });

    return fetch("/api/v1" + path, {
      method: method,
      headers: headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
      credentials: "omit",
    }).then(
      function (response) {
        return response.text().then(function (text) {
          var data = null;
          try {
            data = text ? JSON.parse(text) : null;
          } catch (error) {
            data = null;
          }
          if (response.ok) {
            return data;
          }
          var info = (data && data.error) || {};
          throw new ApiError(
            response.status,
            info.code || "unknown",
            info.message || "Something went wrong. Please try again."
          );
        });
      },
      function () {
        throw new ApiError(
          0,
          "network",
          "We could not reach the service. Please check your connection and try again."
        );
      }
    );
  }

  function newKey() {
    var c = window.crypto;
    if (c && c.randomUUID) {
      return c.randomUUID();
    }
    var bytes = new Uint8Array(16);
    if (c && c.getRandomValues) {
      c.getRandomValues(bytes);
    } else {
      for (var i = 0; i < bytes.length; i += 1) {
        bytes[i] = Math.floor(Math.random() * 256);
      }
    }
    return Array.prototype.map
      .call(bytes, function (b) {
        return (b < 16 ? "0" : "") + b.toString(16);
      })
      .join("");
  }

  function appointmentPath(id, suffix) {
    return "/appointments/" + encodeURIComponent(id) + (suffix || "");
  }

  window.Api = {
    ApiError: ApiError,
    hasToken: function () {
      return token !== null;
    },
    clearToken: function () {
      token = null;
    },
    newKey: newKey,

    notice: function () {
      return request("GET", "/privacy-notice");
    },
    signIn: function (apptId, code) {
      return request("POST", "/auth/patient", { appt_id: apptId, access_code: code }).then(
        function (result) {
          token = result.access_token;
          return result;
        }
      );
    },
    recordConsent: function (type, status, version) {
      return request("POST", "/consents", {
        consent_type: type,
        status: status,
        notice_version: version,
      });
    },
    consents: function () {
      return request("GET", "/consents/me");
    },
    appointment: function (id) {
      return request("GET", appointmentPath(id));
    },
    confirm: function (id) {
      return request("POST", appointmentPath(id, "/confirm"));
    },
    cancel: function (id) {
      return request("POST", appointmentPath(id, "/cancel"), { confirm: true });
    },
    slots: function (id) {
      return request("GET", appointmentPath(id, "/slots"));
    },
    waitTimes: function () {
      return request("GET", "/wait-times");
    },
    reschedule: function (id, slotId, key) {
      return request("POST", appointmentPath(id, "/reschedule"), { new_slot_id: slotId }, {
        "Idempotency-Key": key,
      });
    },
    reminders: function (id) {
      return request("GET", appointmentPath(id, "/reminders"));
    },
  };
})();
