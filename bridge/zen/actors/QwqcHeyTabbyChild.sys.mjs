export class QwqcHeyTabbyChild extends JSWindowActorChild {
  actorCreated() {
    this._tabbyAudioTracks = [];
    this._mediaHookInstalled = false;
    this.installMediaHook();
  }

  didDestroy() {
    this._tabbyAudioTracks = [];
  }

  installMediaHook() {
    if (this._mediaHookInstalled) return true;
    try {
      const rawWin = Cu.waiveXrays(this.contentWindow);
      const mediaDevices = rawWin?.navigator?.mediaDevices;
      if (!mediaDevices || typeof mediaDevices.getUserMedia !== "function") return false;
      const original = mediaDevices.getUserMedia.bind(mediaDevices);
      const actor = this;
      const wrapper = function(constraints) {
        const promise = original(constraints);
        try {
          return promise.then(stream => {
            try {
              const tracks = Array.from(stream?.getAudioTracks?.() || []);
              for (const track of tracks) {
                if (!actor._tabbyAudioTracks.includes(track)) actor._tabbyAudioTracks.push(track);
              }
              actor._tabbyAudioTracks = actor._tabbyAudioTracks.filter(track => track?.readyState !== "ended");
            } catch (_) {}
            return stream;
          });
        } catch (_) {
          return promise;
        }
      };
      Cu.exportFunction(wrapper, mediaDevices, { defineAs: "getUserMedia", allowCrossOriginArguments: true });
      this._mediaHookInstalled = true;
      return true;
    } catch (_) {
      return false;
    }
  }

  audioTrackState() {
    this.installMediaHook();
    const tracks = Array.from(this._tabbyAudioTracks || []).filter(Boolean);
    return tracks.map((track, index) => {
      let settings = {};
      try { settings = track.getSettings?.() || {}; } catch (_) {}
      return {
        index,
        kind: String(track.kind || ""),
        label: String(track.label || ""),
        enabled: Boolean(track.enabled),
        muted: Boolean(track.muted),
        readyState: String(track.readyState || ""),
        deviceId: String(settings.deviceId || ""),
        sampleRate: Number(settings.sampleRate || 0),
        channelCount: Number(settings.channelCount || 0),
      };
    });
  }

  forceAudioTracksOn() {
    this.installMediaHook();
    const tracks = Array.from(this._tabbyAudioTracks || []).filter(track => track && track.readyState !== "ended");
    let changed = 0;
    for (const track of tracks) {
      try {
        if (!track.enabled) { track.enabled = true; changed += 1; }
      } catch (_) {}
    }
    return {
      ok: tracks.length > 0,
      result: tracks.length ? "audio-tracks-enabled" : "no-audio-tracks-captured",
      changed,
      tracks: this.audioTrackState(),
      ...this.publicState(),
    };
  }
  async mediaEnvironment() {
    const rawWin = Cu.waiveXrays(this.contentWindow);
    let devices = [];
    let enumerateError = "";
    try {
      const rawDevices = await Promise.race([
        rawWin.navigator.mediaDevices.enumerateDevices(),
        new Promise((_, reject) =>
          this.contentWindow.setTimeout(() => reject(new Error("enumerateDevices timeout")), 2500)
        ),
      ]);
      devices = Array.from(rawDevices || []).map(d => ({
        kind:String(d.kind || ""), label:String(d.label || ""),
        deviceId:String(d.deviceId || ""), groupId:String(d.groupId || ""),
      }));
    } catch (error) {
      enumerateError = String(error?.name || "") + ": " + String(error?.message || error || "");
    }
    return {
      ok: true, result: "media-environment",
      secureContext: Boolean(rawWin.isSecureContext),
      visibilityState: String(rawWin.document?.visibilityState || ""),
      documentHidden: Boolean(rawWin.document?.hidden),
      documentHasFocus: Boolean(rawWin.document?.hasFocus?.()),
      userActivation: {
        isActive:Boolean(rawWin.navigator?.userActivation?.isActive),
        hasBeenActive:Boolean(rawWin.navigator?.userActivation?.hasBeenActive),
      },
      enumerateError, devices,
      ...this.publicState(),
    };
  }

  async probeMicrophoneMedia() {
    const rawWin = Cu.waiveXrays(this.contentWindow);
    const mediaDevices = rawWin?.navigator?.mediaDevices;
    if (!mediaDevices || typeof mediaDevices.getUserMedia !== "function")
      return { ok:false, result:"getusermedia-unavailable", ...this.publicState() };
    let handlingUserInput = null;
    let activationDuring = { isActive:false, hasBeenActive:false };
    try {
      try { handlingUserInput = this.contentWindow.windowUtils.setHandlingUserInput(true); } catch (_) {}
      activationDuring = {
        isActive:Boolean(rawWin.navigator?.userActivation?.isActive),
        hasBeenActive:Boolean(rawWin.navigator?.userActivation?.hasBeenActive),
      };
      const constraints = Cu.cloneInto({ audio:true, video:false }, rawWin);
      let timedOut = false;
      const gum = mediaDevices.getUserMedia(constraints);
      try {
        gum.then(stream => {
          if (!timedOut) return;
          for (const track of Array.from(stream?.getTracks?.() || [])) {
            try { track.stop(); } catch (_) {}
          }
        }).catch(() => {});
      } catch (_) {}
      const timeout = new Promise((_, reject) =>
        this.contentWindow.setTimeout(() => {
          timedOut = true;
          reject(new Error("getUserMedia timeout"));
        }, 4500)
      );
      const stream = await Promise.race([gum, timeout]);
      const tracks = Array.from(stream?.getAudioTracks?.() || []);
      const info = tracks.map(track => {
        let settings = {};
        try { settings = track.getSettings?.() || {}; } catch (_) {}
        return {
          kind:String(track.kind || ""), label:String(track.label || ""),
          enabled:Boolean(track.enabled), muted:Boolean(track.muted),
          readyState:String(track.readyState || ""),
          deviceId:String(settings.deviceId || ""), sampleRate:Number(settings.sampleRate || 0),
          channelCount:Number(settings.channelCount || 0),
        };
      });
      for (const track of tracks) { try { track.stop(); } catch (_) {} }
      return { ok:true, result:"getusermedia-ok", activationDuring, tracks:info, ...this.publicState() };
    } catch (error) {
      return {
        ok:false,
        result:String(error?.message || "").includes("timeout") ? "getusermedia-timeout" : "getusermedia-failed",
        activationDuring,
        errorName:String(error?.name || ""), errorMessage:String(error?.message || error || ""),
        ...this.publicState(),
      };
    } finally {
      try { handlingUserInput?.destruct?.(); } catch (_) {}
      try { if (!handlingUserInput) this.contentWindow.windowUtils.setHandlingUserInput(false); } catch (_) {}
    }
  }


  static labelFor(element) {
    if (!element) return "";
    return [
      element.getAttribute?.("aria-label") || "",
      element.getAttribute?.("title") || "",
      element.getAttribute?.("data-testid") || "",
      element.textContent || "",
    ].join(" ").replace(/\s+/g, " ").trim().toLowerCase();
  }

  visible(element) {
    if (!element) return false;
    const style = this.contentWindow.getComputedStyle(element);
    const rect = element.getBoundingClientRect?.();
    return style?.display !== "none" && style?.visibility !== "hidden" && (!rect || (rect.width > 0 && rect.height > 0));
  }

  candidates() {
    const document = this.document;
    if (!document) return [];
    return Array.from(document.querySelectorAll(
      'button, [role="button"], a, [data-testid*="voice"], [aria-label*="voice" i], [aria-label*="stimm" i], [aria-label*="sprach" i]'
    )).filter(element => this.visible(element))
      .map(element => ({ element, label: QwqcHeyTabbyChild.labelFor(element) }));
  }

  findComposer() {
    const doc = this.document;
    if (!doc) return null;
    const selectors = [
      '#prompt-textarea',
      'textarea[data-testid*="prompt"]',
      'textarea[placeholder]',
      '[contenteditable="true"][data-testid*="composer"]',
      'div[contenteditable="true"][role="textbox"]',
      '[contenteditable="true"]',
    ];
    for (const selector of selectors) {
      const nodes = Array.from(doc.querySelectorAll(selector));
      const hit = nodes.find(node => this.visible(node));
      if (hit) return hit;
    }
    return null;
  }

  findSendButton() {
    const doc = this.document;
    if (!doc) return null;
    const candidates = Array.from(doc.querySelectorAll('button,[role="button"]'))
      .filter(el => this.visible(el))
      .map(el => ({ el, label: QwqcHeyTabbyChild.labelFor(el) }));
    return candidates.find(({ el, label }) =>
      el.getAttribute?.('data-testid') === 'send-button' ||
      /^(send|send prompt|send message|submit)$/.test(label) ||
      /send.*(prompt|message)/.test(label)
    )?.el || null;
  }

  state() {
    const entries = this.candidates();
    const href = this.document?.location?.href || "";
    const loggedOut = entries.some(({ label, element }) => {
      const link = element.getAttribute?.("href") || "";
      return /^(log in|login|sign up|sign up for free|log in or create account)$/.test(label) || /\/auth\/login/.test(link);
    });
    const activeControl = entries.find(({ label }) =>
      /((end|stop|leave|exit|close).*(voice|sprach|stimm))|((voice|sprach|stimm).*(end|stop|leave|exit|close))/.test(label)
    );
    const startControl = entries.find(({ label, element }) => {
      const link = element.getAttribute?.("href") || "";
      return label === "start voice" ||
        /(start|open|enter|begin).*(voice|sprach|stimm)/.test(label) ||
        /[?&]mode=voice(?:$|&)/.test(link);
    });
    const active = Boolean(activeControl) || /[?&]mode=voice(?:$|&)/.test(href);
    const micOffControl = entries.find(({ label }) =>
      /^(turn on microphone|unmute microphone|unmute mic|microphone off|mic off)$/.test(label) ||
      /(turn on|unmute).*(microphone|mic)/.test(label)
    );
    const micOnControl = entries.find(({ label }) =>
      /^(turn off microphone|mute microphone|mute mic|microphone on|mic on)$/.test(label) ||
      /(turn off|mute).*(microphone|mic)/.test(label)
    );

    const busyElement = this.document?.querySelector?.('[data-testid*="stop-button"], .result-streaming');
    const workingButton = entries.find(({ label }) =>
      /(stop generating|stop response|cancel response|interrupt response|searching the web|working on your request)/.test(label)
    );
    const bodyText = String(this.document?.body?.innerText || "").toLowerCase();
    const connectionInterrupted = /(connection interrupted|waiting for the complete answer|network error|something went wrong)/.test(bodyText);
    // During a connection-interrupted turn ChatGPT currently exposes the
    // composer abort control simply as aria-label="Stop", without the usual
    // "Stop generating" wording or data-testid. Treat that generic Stop as a
    // generation control only while the interruption banner is present.
    const interruptedStop = connectionInterrupted
      ? entries.find(({ label }) => label === "stop")
      : null;
    // Voice mode itself contains persistent controls such as the model's
    // "Thinking effort" selector. Those are metadata, not evidence that the
    // assistant is currently thinking. While Voice is active the standalone
    // Tabby backend derives speaking/listening from real output amplitude.
    const working = !active && Boolean((busyElement && this.visible(busyElement)) || workingButton || interruptedStop);

    return {
      active,
      ready: Boolean(startControl),
      loggedOut,
      working,
      connectionInterrupted,
      workingControl: workingButton?.element || interruptedStop?.element || ((busyElement && this.visible(busyElement)) ? busyElement : null),
      composerReady: Boolean(this.findComposer()),
      href,
      title: this.document?.title || "",
      activeControl: activeControl?.element || null,
      startControl: startControl?.element || null,
      micMuted: Boolean(micOffControl),
      micOffControl: micOffControl?.element || null,
      micOnControl: micOnControl?.element || null,
    };
  }

  trustedClick(element) {
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    const x = Math.max(1, rect.left + rect.width / 2);
    const y = Math.max(1, rect.top + rect.height / 2);
    try {
      const utils = this.contentWindow.windowUtils;
      utils.sendMouseEvent("mousemove", x, y, 0, 0, 0);
      utils.sendMouseEvent("mousedown", x, y, 0, 1, 0);
      utils.sendMouseEvent("mouseup", x, y, 0, 1, 0);
      return true;
    } catch (_) {
      try {
        element.focus?.({ preventScroll: true });
        element.click();
        return true;
      } catch (_) {
        return false;
      }
    }
  }

  async waitForActive(timeoutMs = 10000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const state = this.state();
      if (state.active) return state;
      await new Promise(resolve => this.contentWindow.setTimeout(resolve, 180));
    }
    return this.state();
  }

  async waitForComposer(timeoutMs = 10000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const composer = this.findComposer();
      if (composer) return composer;
      await new Promise(resolve => this.contentWindow.setTimeout(resolve, 140));
    }
    return this.findComposer();
  }

  async stopResponse() {
    const before = this.state();
    if (!before.working)
      return { ok:true, result:"not-working", ...this.publicState(before) };
    const control = before.workingControl;
    if (!control)
      return { ok:false, result:"stop-response-control-not-found", ...this.publicState(before) };
    const clicked = this.trustedClick(control);
    await new Promise(resolve => this.contentWindow.setTimeout(resolve, 140));
    return { ok:Boolean(clicked), result:clicked ? "response-stop-requested" : "response-stop-click-failed", ...this.publicState() };
  }

  async activateVoice() {
    // Do not wait for the active Voice UI inside this WindowActor query.
    // ChatGPT rehydrates/navigates the Voice surface after the click, which
    // can destroy or replace this actor before the query resolves. The parent
    // bridge performs the active-state polling against the freshly attached
    // actor instead.
    for (let attempt = 0; attempt < 70; attempt++) {
      const state = this.state();
      if (state.active) return { ok: true, result: "already-active", ...this.publicState(state) };
      if (state.startControl) {
        if (!this.trustedClick(state.startControl))
          return { ok: false, result: "voice-click-failed", ...this.publicState(state) };
        return { ok: true, result: "starting", ...this.publicState(state) };
      }
      await new Promise(resolve => this.contentWindow.setTimeout(resolve, 120));
    }
    const state = this.state();
    return { ok: false, result: state.loggedOut ? "needs-login" : "voice-button-not-found", ...this.publicState(state) };
  }

  publicState(state = this.state()) {
    return {
      active: Boolean(state.active),
      ready: Boolean(state.ready),
      loggedOut: Boolean(state.loggedOut),
      working: Boolean(state.working),
      connectionInterrupted: Boolean(state.connectionInterrupted),
      composerReady: Boolean(state.composerReady),
      micMuted: Boolean(state.micMuted),
      audioTracks: this.audioTrackState?.() || [],
      href: state.href || "",
      title: state.title || "",
    };
  }

  armMicEventProbe() {
    const state = this.state();
    const button = state.micOffControl || state.micOnControl || null;
    if (!button) return { ok:false, result:"microphone-control-not-found", ...this.publicState(state) };
    this._micProbeEvents = [];
    if (this._micProbeButton && this._micProbeHandlers) {
      for (const [type, handler] of this._micProbeHandlers) {
        try { this._micProbeButton.removeEventListener(type, handler, true); } catch (_) {}
      }
    }
    const handlers = [];
    for (const type of ["pointerdown","pointerup","mousedown","mouseup","click","keydown","keyup"]) {
      const handler = event => {
        try {
          this._micProbeEvents.push({
            type,
            isTrusted: Boolean(event.isTrusted),
            targetAria: event.target?.getAttribute?.("aria-label") || "",
            currentAria: event.currentTarget?.getAttribute?.("aria-label") || "",
            clientX: Number(event.clientX || 0), clientY: Number(event.clientY || 0),
            key: String(event.key || ""), code: String(event.code || ""),
            defaultPrevented: Boolean(event.defaultPrevented),
            userActivation: {
              isActive: Boolean(this.contentWindow.navigator?.userActivation?.isActive),
              hasBeenActive: Boolean(this.contentWindow.navigator?.userActivation?.hasBeenActive),
            },
            timestamp: Date.now(),
          });
        } catch (_) {}
      };
      button.addEventListener(type, handler, true);
      handlers.push([type, handler]);
    }
    this._micProbeButton = button;
    this._micProbeHandlers = handlers;
    return { ok:true, result:"mic-probe-armed", ...this.publicState(state) };
  }

  micEventProbeState() {
    return { ok:true, result:"mic-probe-state", events:Array.from(this._micProbeEvents || []), ...this.publicState() };
  }

  focusPage() {
    try { this.contentWindow.focus?.(); } catch (_) {}
    try { this.document?.documentElement?.focus?.({ preventScroll:true }); } catch (_) {}
    const focused = Boolean(this.document?.hasFocus?.());
    return {
      ok: focused,
      result: focused ? "page-focused" : "page-focus-failed",
      documentHasFocus: focused,
      ...this.publicState(),
    };
  }

  focusMicControl() {
    const state = this.state();
    const button = state.micOffControl || state.micOnControl || null;
    if (!button) return { ok:false, result:"microphone-control-not-found", ...this.publicState(state) };
    try {
      button.focus({ preventScroll:true });
      const active = this.document?.activeElement;
      const rect = button.getBoundingClientRect();
      return {
        ok: active === button,
        result: active === button ? "microphone-focused" : "microphone-focus-failed",
        activeAria: active?.getAttribute?.("aria-label") || "",
        micRect: { x:rect.x, y:rect.y, width:rect.width, height:rect.height },
        ...this.publicState(),
      };
    } catch (error) {
      return { ok:false, result:"microphone-focus-error", error:String(error), ...this.publicState(state) };
    }
  }

  async ensureMicrophoneOn() {
    const before = this.state();
    if (!before.active)
      return { ok:false, result:"voice-not-active", ...this.publicState(before) };
    if (!before.micMuted)
      return { ok:true, result:"microphone-on", ...this.publicState(before) };
    const button = before.micOffControl;
    if (!button)
      return { ok:false, result:"microphone-control-not-found", ...this.publicState(before) };

    let handlingUserInput = null;
    let activationDuring = { isActive:false, hasBeenActive:false };
    let clickError = "";
    try {
      const utils = this.contentWindow.windowUtils;
      try { handlingUserInput = utils.setHandlingUserInput(true); } catch (_) {}
      activationDuring = {
        isActive:Boolean(this.contentWindow.navigator?.userActivation?.isActive),
        hasBeenActive:Boolean(this.contentWindow.navigator?.userActivation?.hasBeenActive),
      };
      button.focus?.({ preventScroll:true });
      // Use a trusted Gecko click while inside the privileged user-input scope.
      const rect = button.getBoundingClientRect();
      const x = Math.max(1, rect.left + rect.width / 2);
      const y = Math.max(1, rect.top + rect.height / 2);
      try {
        utils.sendMouseEvent("mousedown", x, y, 0, 1, 0);
        utils.sendMouseEvent("mouseup", x, y, 0, 1, 0);
        utils.sendMouseEvent("click", x, y, 0, 1, 0);
      } catch (error) {
        clickError = String(error);
        try { button.click(); } catch (_) {}
      }
    } catch (error) {
      clickError = String(error);
    } finally {
      try { handlingUserInput?.destruct?.(); } catch (_) {}
      try { if (!handlingUserInput) this.contentWindow.windowUtils.setHandlingUserInput(false); } catch (_) {}
    }

    await new Promise(resolve => this.contentWindow.setTimeout(resolve, 550));
    const after = this.state();
    return {
      ok: !after.micMuted,
      result: after.micMuted ? "microphone-still-muted" : "microphone-enabled",
      activationDuring,
      activationAfter: {
        isActive:Boolean(this.contentWindow.navigator?.userActivation?.isActive),
        hasBeenActive:Boolean(this.contentWindow.navigator?.userActivation?.hasBeenActive),
      },
      clickError,
      ...this.publicState(after),
    };
  }

  async endVoice() {
    const state = this.state();
    if (!state.active) return { ok: true, result: "already-inactive", ...this.publicState(state) };
    if (state.activeControl) this.trustedClick(state.activeControl);
    const deadline = Date.now() + 1200;
    let after = this.state();
    while (after.active && Date.now() < deadline) {
      await new Promise(resolve => this.contentWindow.setTimeout(resolve, 20));
      after = this.state();
    }
    return { ok: !after.active, result: after.active ? "end-click-failed" : "ended", ...this.publicState(after) };
  }

  setComposerText(composer, text) {
    composer.focus?.({ preventScroll: true });
    if (composer instanceof this.contentWindow.HTMLTextAreaElement || composer instanceof this.contentWindow.HTMLInputElement) {
      const proto = composer instanceof this.contentWindow.HTMLTextAreaElement
        ? this.contentWindow.HTMLTextAreaElement.prototype
        : this.contentWindow.HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
      if (setter) setter.call(composer, text);
      else composer.value = text;
    } else {
      composer.textContent = text;
    }
    try {
      composer.dispatchEvent(new this.contentWindow.InputEvent("input", {
        bubbles: true,
        cancelable: false,
        inputType: "insertText",
        data: text,
      }));
    } catch (_) {
      composer.dispatchEvent(new this.contentWindow.Event("input", { bubbles: true }));
    }
    composer.dispatchEvent(new this.contentWindow.Event("change", { bubbles: true }));
  }

  async clearComposer() {
    const composer = await this.waitForComposer();
    if (!composer) return { ok: false, result: "composer-not-found", ...this.publicState() };
    this.setComposerText(composer, "");
    // Some contenteditable implementations retain a lone <br>/newline after
    // clearing. Dispatch Backspace once through the trusted page window and
    // then normalize the DOM again so ChatGPT sees a truly empty prompt.
    try {
      composer.focus?.({ preventScroll: true });
      const utils = this.contentWindow.windowUtils;
      utils.sendKeyEvent("keydown", 8, 0, 0);
      utils.sendKeyEvent("keyup", 8, 0, 0);
    } catch (_) {}
    if (!(composer instanceof this.contentWindow.HTMLTextAreaElement) && !(composer instanceof this.contentWindow.HTMLInputElement)) {
      composer.textContent = "";
      composer.innerHTML = "";
      composer.dispatchEvent(new this.contentWindow.Event("input", { bubbles: true }));
    }
    await new Promise(resolve => this.contentWindow.setTimeout(resolve, 220));
    let length = 0;
    if ("value" in composer) length = String(composer.value || "").length;
    else length = String(composer.innerText || composer.textContent || "").trim().length;
    return { ok: length === 0, result: length === 0 ? "composer-cleared" : "composer-not-empty", composerTextLength: length, ...this.publicState() };
  }

  async sendText(text) {
    const composer = await this.waitForComposer();
    if (!composer) return { ok: false, result: "composer-not-found", ...this.publicState() };
    const clean = String(text ?? "");
    if (clean.length > 0) this.setComposerText(composer, clean);
    let send = null;
    const deadline = Date.now() + 8000;
    while (Date.now() < deadline) {
      send = this.findSendButton();
      const disabled = send && (send.disabled || send.getAttribute?.("aria-disabled") === "true");
      if (send && !disabled) break;
      send = null;
      await new Promise(resolve => this.contentWindow.setTimeout(resolve, 100));
    }
    if (!send) return { ok: false, result: "send-button-not-found", ...this.publicState() };
    if (!this.trustedClick(send)) return { ok: false, result: "send-click-failed", ...this.publicState() };
    await new Promise(resolve => this.contentWindow.setTimeout(resolve, 180));
    return { ok: true, result: "sent", ...this.publicState() };
  }

  async newChat() {
    try {
      // Always hard-navigate Tabby's dedicated tab to the canonical fresh
      // composer. This never touches the user's normal selected ChatGPT tab.
      this.contentWindow.location.assign("https://chatgpt.com/?tabby=1");
      return { ok: true, result: "navigating" };
    } catch (error) {
      return { ok: false, result: "new-chat-navigation-failed", error: String(error) };
    }
  }

  async pasteImage(payload) {
    const composer = await this.waitForComposer();
    if (!composer) return { ok: false, result: "composer-not-found", ...this.publicState() };
    const b64 = String(payload?.base64 || "");
    if (!b64) return { ok: false, result: "missing-image", ...this.publicState() };
    try {
      const binary = this.contentWindow.atob(b64);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      const mime = String(payload?.mime || "image/png");
      const name = String(payload?.name || "tabby-paste.png");
      const file = new this.contentWindow.File([bytes], name, { type: mime });
      const transfer = new this.contentWindow.DataTransfer();
      transfer.items.add(file);

      const findInputs = () => Array.from(this.document.querySelectorAll('input[type="file"]'));
      let inputs = findInputs();
      // ChatGPT hydrates its hidden attachment inputs slightly after the text
      // composer. Wait for that normal path first instead of racing it.
      let inputDeadline = Date.now() + 3500;
      while (!inputs.length && Date.now() < inputDeadline) {
        await new Promise(resolve => this.contentWindow.setTimeout(resolve, 100));
        inputs = findInputs();
      }
      if (!inputs.length) {
        const add = this.candidates().find(({ label }) =>
          /(add files and more|attach files|add files|upload|paperclip)/.test(label)
        );
        if (add?.element) {
          this.trustedClick(add.element);
          inputDeadline = Date.now() + 3000;
          while (Date.now() < inputDeadline) {
            inputs = findInputs();
            if (inputs.length) break;
            await new Promise(resolve => this.contentWindow.setTimeout(resolve, 100));
          }
        }
      }

      let fileInput = inputs.find(input => {
        const accept = String(input.getAttribute("accept") || "").toLowerCase();
        return accept === "" || accept.includes("image") || accept.includes("*");
      }) || inputs[0] || null;

      let method = "paste";
      if (fileInput) {
        try {
          fileInput.files = transfer.files;
          fileInput.dispatchEvent(new this.contentWindow.Event("input", { bubbles: true }));
          fileInput.dispatchEvent(new this.contentWindow.Event("change", { bubbles: true }));
          method = "file-input";
        } catch (_) {
          fileInput = null;
        }
      }

      if (method === "paste") {
        composer.focus?.({ preventScroll: true });
        const event = new this.contentWindow.ClipboardEvent("paste", {
          clipboardData: transfer,
          bubbles: true,
          cancelable: true,
          composed: true,
        });
        composer.dispatchEvent(event);
      }

      const deadline = Date.now() + 7000;
      let attachment = null;
      let byName = false;
      while (Date.now() < deadline) {
        attachment = this.document.querySelector(
          '[data-testid*="attachment"], [aria-label*="remove file" i], [aria-label*="remove image" i], [data-testid*="file"] img, img[src^="blob:"], img[alt*="upload" i]'
        );
        byName = String(this.document.body?.innerText || "").includes(name);
        if (attachment || byName) break;
        await new Promise(resolve => this.contentWindow.setTimeout(resolve, 160));
      }
      return {
        ok: Boolean(attachment || byName),
        result: (attachment || byName) ? "image-attached" : "image-attachment-not-detected",
        method,
        attachmentDetected: Boolean(attachment || byName),
        fileInputCount: inputs.length,
        ...this.publicState(),
      };
    } catch (error) {
      return { ok: false, result: "image-paste-failed", error: String(error), ...this.publicState() };
    }
  }

  readLatestAloud() {
    const doc = this.document;
    if (!doc) return { ok:false, result:"document-unavailable", ...this.publicState() };
    const buttons = Array.from(doc.querySelectorAll('button,[role="button"]'))
      .filter(el => this.visible(el) && QwqcHeyTabbyChild.labelFor(el) === "read aloud");
    const button = buttons[buttons.length - 1] || null;
    if (!button) return { ok:false, result:"read-aloud-not-found", ...this.publicState() };
    try {
      button.focus?.({ preventScroll:true });
      button.click();
      return { ok:true, result:"read-aloud-started", ...this.publicState() };
    } catch (_) {
      const ok = this.trustedClick(button);
      return { ok, result:ok ? "read-aloud-started" : "read-aloud-click-failed", ...this.publicState() };
    }
  }

  latestAssistantResponse() {
    const doc = this.document;
    if (!doc) return { ok: false, result: "document-unavailable", assistantCount: 0, assistantText: "" };

    const semantic = Array.from(doc.querySelectorAll('[data-message-author-role="assistant"]'));
    let text = "";
    let messageId = "";
    let assistantCount = semantic.length;

    if (semantic.length) {
      const latest = semantic[semantic.length - 1];
      const content = latest.querySelector(
        '.markdown, [class*="markdown"], [data-message-content], [class*="prose"]'
      ) || latest;
      text = String(content.innerText || content.textContent || "").trim();
      const host = latest.closest?.('[data-message-id]') || latest;
      messageId = String(host?.getAttribute?.('data-message-id') || "");
    } else {
      // The current ChatGPT renderer virtualizes turns without message-role
      // attributes. Assistant action controls remain stable, so anchor on the
      // last response's Read aloud / Regenerate controls and walk to its turn.
      const controls = Array.from(doc.querySelectorAll('button,[role="button"]')).filter(el => this.visible(el));
      const responseAnchors = controls.filter(el => {
        const label = QwqcHeyTabbyChild.labelFor(el);
        return /^(read aloud|regenerate response)$/.test(label);
      });
      assistantCount = responseAnchors.filter(el => QwqcHeyTabbyChild.labelFor(el) === 'read aloud').length;
      const anchor = responseAnchors[responseAnchors.length - 1] || null;
      let turn = anchor;
      for (let i = 0; turn && i < 10; i++, turn = turn.parentElement) {
        const raw = String(turn.innerText || turn.textContent || "").trim();
        const marker = raw.lastIndexOf("ChatGPT said:");
        if (marker >= 0) {
          text = raw.slice(marker + "ChatGPT said:".length).trim();
          const nextUser = text.indexOf("\nYou said:");
          if (nextUser >= 0) text = text.slice(0, nextUser).trim();
          break;
        }
      }
    }

    text = text
      .replace(/\n(?:Copy|Share|Read aloud|Regenerate response|React|Bad response|More actions)(?:\n.*)*$/i, "")
      .replace(/\n*Is this conversation helpful so far\?.*$/i, "")
      .trim();
    return {
      ok: true,
      result: text ? "latest-assistant-response" : "no-assistant-message",
      assistantCount,
      assistantText: text.slice(0, 12000),
      assistantMessageId: messageId,
      ...this.publicState(),
    };
  }

  debugMicControl() {
    const state = this.state();
    let el = state.micOffControl || state.micOnControl || null;
    const rows = [];
    for (let depth = 0; el && depth < 7; depth++, el = el.parentElement) {
      let reactKeys = [];
      let functionProps = [];
      let propKeys = [];
      try {
        const raw = Cu.waiveXrays(el);
        reactKeys = Reflect.ownKeys(raw).map(String).filter(k => k.startsWith("__react")).slice(0, 20);
        const propKey = reactKeys.find(k => k.startsWith("__reactProps$"));
        const props = propKey ? raw[propKey] : null;
        if (props) {
          propKeys = Reflect.ownKeys(props).map(String).slice(0, 80);
          functionProps = Reflect.ownKeys(props).filter(k => typeof props[k] === "function").map(String).slice(0, 40);
        }
      } catch (_) {}
      let rect = null;
      try {
        const r = el.getBoundingClientRect();
        rect = { x:r.x, y:r.y, width:r.width, height:r.height };
      } catch (_) {}
      rows.push({
        depth,
        tag: el.tagName,
        aria: el.getAttribute?.("aria-label") || "",
        role: el.getAttribute?.("role") || "",
        testid: el.getAttribute?.("data-testid") || "",
        cls: String(el.className || "").slice(0, 260),
        disabled: Boolean(el.disabled),
        rect,
        reactKeys,
        propKeys,
        functionProps,
        html: String(el.outerHTML || "").slice(0, 1200),
      });
    }
    return { ok:true, result:"debug-mic", rows, ...this.publicState(state) };
  }

  debugAllControls() {
    const doc = this.document;
    const rows = Array.from(doc?.querySelectorAll?.('button,[role="button"],input,textarea,[contenteditable="true"]') || [])
      .filter(el => this.visible(el) || el.tagName === "INPUT")
      .slice(0, 180)
      .map(el => ({
        tag: el.tagName,
        type: el.getAttribute?.("type") || "",
        label: QwqcHeyTabbyChild.labelFor(el).slice(0, 180),
        testid: el.getAttribute?.("data-testid") || "",
        aria: el.getAttribute?.("aria-label") || "",
        title: el.getAttribute?.("title") || "",
      }));
    return { ok:true, result:"debug-all", count:rows.length, rows, ...this.publicState() };
  }

  debugComposer() {
    const doc = this.document;
    const composer = this.findComposer();
    let composerTextLength = 0;
    if (composer) {
      if ("value" in composer) composerTextLength = String(composer.value || "").length;
      else composerTextLength = String(composer.innerText || composer.textContent || "").length;
    }
    const send = this.findSendButton();
    const buttons = Array.from(doc?.querySelectorAll?.('button,[role="button"],input') || [])
      .filter(el => this.visible(el) || el.tagName === "INPUT")
      .map(el => ({
        tag: el.tagName,
        type: el.getAttribute?.("type") || "",
        label: QwqcHeyTabbyChild.labelFor(el).slice(0, 180),
        accept: el.getAttribute?.("accept") || "",
        testid: el.getAttribute?.("data-testid") || "",
      }))
      .filter(x => /attach|file|photo|image|upload|add|plus|paperclip|clip|voice|dictat|send/.test(JSON.stringify(x).toLowerCase()) || x.type === "file")
      .slice(0, 80);
    return {
      ok: true,
      result: "debug",
      composerTextLength,
      composerEmpty: composerTextLength === 0,
      sendPresent: Boolean(send),
      sendDisabled: send ? Boolean(send.disabled || send.getAttribute?.("aria-disabled") === "true") : null,
      buttons,
      ...this.publicState(),
    };
  }

  async receiveMessage(message) {
    switch (message.name) {
      case "activateVoice": return this.activateVoice();
      case "stopResponse": return this.stopResponse();
      case "ensureMicrophoneOn": return this.ensureMicrophoneOn();
      case "audioTrackState": return { ok:true, result:"audio-track-state", tracks:this.audioTrackState(), ...this.publicState() };
      case "forceAudioTracksOn": return this.forceAudioTracksOn();
      case "probeMicrophoneMedia": return this.probeMicrophoneMedia();
      case "mediaEnvironment": return this.mediaEnvironment();
      case "armMicEventProbe": return this.armMicEventProbe();
      case "micEventProbeState": return this.micEventProbeState();
      case "focusPage": return this.focusPage();
      case "focusMicControl": return this.focusMicControl();
      case "endVoice": return this.endVoice();
      case "newChat": return this.newChat();
      case "clearComposer": return this.clearComposer();
      case "sendText": return this.sendText(message.data?.text ?? "");
      case "pasteImage": return this.pasteImage(message.data || {});
      case "latestAssistantResponse": return this.latestAssistantResponse();
      case "readLatestAloud": return this.readLatestAloud();
      case "debugComposer": return this.debugComposer();
      case "debugMicControl": return this.debugMicControl();
      case "debugAllControls": return this.debugAllControls();
      case "voiceStatus": return { ok: true, result: "status", ...this.publicState() };
      default: return { ok: false, result: "unknown-message" };
    }
  }
}
