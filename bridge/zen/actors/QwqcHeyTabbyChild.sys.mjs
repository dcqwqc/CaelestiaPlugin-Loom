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

  static projectLabels(element) {
    if (!element) return [];
    return [element.innerText, element.textContent, element.getAttribute?.("aria-label")]
      .map(value => String(value || "").replace(/\s+/g, " ").trim().toLowerCase())
      .filter(Boolean);
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
      const testId = String(element.getAttribute?.("data-testid") || "").toLowerCase();
      const isButton = element.tagName === "BUTTON" || element.getAttribute?.("role") === "button";
      // ChatGPT also ships an icon-only "voice-button" and a bare "Voice"
      // control. Do not depend on the old English "Start voice" translation.
      // Restrict short labels to buttons to avoid matching navigation links.
      return label === "start voice" ||
        /(start|open|enter|begin|use|launch).*(voice|sprach|stimm)/.test(label) ||
        (isButton && /^(voice|voice mode|voice chat|sprachmodus|spracheingabe)$/.test(label)) ||
        /(^|[-_])voice([-_](button|mode|start|chat))?$/i.test(testId) ||
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

  radixPointerDown(element) {
    // Radix menus listen for pointerdown, not synthetic mouse click.
    // Trigger only a previously identity-matched DOM element, never the
    // user's physical pointer or an unrelated ChatGPT sidebar control.
    if (!element) return false;
    try {
      const Type = this.contentWindow?.PointerEvent;
      if (Type && typeof element.dispatchEvent === 'function') {
        const rect = element.getBoundingClientRect();
        element.dispatchEvent(new Type('pointerdown', {bubbles:true,cancelable:true,
          pointerType:'mouse',pointerId:1,isPrimary:true,button:0,buttons:1,
          clientX:rect.left+rect.width/2,clientY:rect.top+rect.height/2}));
        return true;
      }
    } catch (_) {}
    return this.trustedClick(element);
  }

  selectRadixItem(element) {
    if (!element) return false;
    try {
      if (this.contentWindow?.PointerEvent && typeof element.click === 'function') {
        element.click();
        return true;
      }
    } catch (_) {}
    return this.trustedClick(element);
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

  // Read-only acknowledgement from the conversation, not a click or URL change.
  // The composer alone never counts as proof that the prompt was delivered.
  promptSubmissionState(text) {
    const wanted = String(text || "").replace(/\s+/g, " ").trim();
    if (!wanted) return {ok:false,result:"empty-prompt"};
    const userTurns = Array.from(this.document?.querySelectorAll?.('[data-message-author-role="user"]') || []);
    const match = userTurns.some(node =>
      String(node.innerText || node.textContent || "").replace(/\s+/g, " ").trim() === wanted);
    // ChatGPT can render turns without author-role attributes. Only accept
    // the explicit "You said:" turn label, never a draft in the composer.
    const bodyText = String(this.document?.body?.innerText || "");
    const paragraphs = bodyText.split(/You said:\s*/i).slice(1);
    const fallback = paragraphs.some(part => {
      const normalized = part.replace(/\s+/g, " ").trim();
      return normalized === wanted || normalized.startsWith(wanted + " ChatGPT said:");
    });
    const acknowledged = match || fallback;
    return {ok:true,result:acknowledged?"prompt-acknowledged":"prompt-unverified",
      promptAcknowledged:acknowledged, userTurnCount:userTurns.length,
      ...this.publicState()};
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

  // ChatGPT project route segments look like `g-p-<32 hex>-<slug>`. The hex
  // prefix is the stable project identity; the slug follows the display name
  // and may change on rename. Custom GPTs use `/g/g-<id>` without `-p-` and
  // are never treated as projects.
  static projectCoreId(segment) {
    const raw = String(segment || "").trim();
    const match = raw.match(/^(g-p-[0-9a-f]{32})(?:-|$)/i);
    return match ? match[1].toLowerCase() : raw;
  }

  static parseChatRoute(href) {
    let url;
    try { url = new URL(String(href || "")); } catch (_) { return null; }
    if (url.origin !== "https://chatgpt.com") return null;
    const parts = url.pathname.split("/").filter(Boolean);
    if (parts.length === 2 && parts[0] === "c" && parts[1])
      return { conversationId: parts[1], projectSegment: "", projectId: "" };
    if (parts.length === 4 && parts[0] === "g" && parts[2] === "c" && parts[1] && parts[3])
      return { conversationId: parts[3], projectSegment: parts[1],
               projectId: QwqcHeyTabbyChild.projectCoreId(parts[1]) };
    return null;
  }

  static projectName(el) {
    const raw = String(el?.innerText || el?.textContent || el?.getAttribute?.("aria-label") || "");
    return raw.split("\n").map(line => line.trim()).find(Boolean) || "";
  }

  // The current ChatGPT sidebar renders projects as button rows without
  // links; each row carries "New chat in <name>" and "Project actions for
  // <name>" controls. Names come from those labels; ids are only learned by
  // opening the project (see openProject) and reading the route.
  static sidebarProjectNames(doc) {
    const names = [];
    for (const el of doc?.querySelectorAll?.('button,[role="button"]') || []) {
      const match = String(el.getAttribute?.("aria-label") || "").trim().match(/^new chat in (.+)$/i);
      if (match && !names.some(n => n.toLowerCase() === match[1].trim().toLowerCase())) names.push(match[1].trim());
    }
    return names;
  }

  openProject(projectName) {
    const name = String(projectName || "").replace(/\s+/g, " ").trim().toLowerCase();
    if (!name || name.length > 160) return { ok:false, result:"invalid-project-name" };
    const controls = Array.from(this.document.querySelectorAll('button,[role="button"]')).filter(el => this.visible(el));
    const labelled = controls.filter(el =>
      String(el.getAttribute?.("aria-label") || "").replace(/\s+/g, " ").trim().toLowerCase() === `new chat in ${name}`);
    if (labelled.length > 1) return { ok:false, result:"project-control-ambiguous", candidates:labelled.length };
    let target = labelled[0], via = "new-chat-control";
    if (!target) {
      // Fallback: the project row itself, matched on its own visible text.
      const rows = controls.filter(el => !/^(new chat in|project actions for) /i.test(String(el.getAttribute?.("aria-label") || ""))
        && QwqcHeyTabbyChild.projectName(el).toLowerCase() === name);
      if (rows.length > 1) return { ok:false, result:"project-control-ambiguous", candidates:rows.length };
      target = rows[0]; via = "sidebar-row";
    }
    if (!target) return { ok:false, result:"project-control-not-found" };
    const previousUrl = String(this.contentWindow.location.href || "");
    if (!this.trustedClick(target)) return { ok:false, result:"project-control-click-failed" };
    return { ok:true, result:"project-open-requested", via, previousUrl };
  }

  discoverProjects() {
    const seen = new Map();
    // ChatGPT now exposes the exact project id alongside its sidebar label.
    // This is stronger than guessing from chat URLs or navigating a hidden
    // window. The actual move remains independently verified by canonical URL.
    for (const row of this.document.querySelectorAll('[data-app-action-sidebar-project-id]')) {
      const id = QwqcHeyTabbyChild.projectCoreId(row.getAttribute?.('data-app-action-sidebar-project-id'));
      const name = String(row.getAttribute?.('data-app-action-sidebar-project-label') || '').replace(/\s+/g, ' ').trim();
      if (/^g-p-[0-9a-f]{32}$/i.test(id) && name && !seen.has(id))
        seen.set(id, {id,segment:id,name:name.slice(0,160),via:'sidebar-row-id'});
    }
    for (const el of this.document.querySelectorAll('a[href*="/g/"],a[href*="/project"],button,[role="menuitem"]')) {
      const name = QwqcHeyTabbyChild.projectName(el);
      const href = String(el.href || el.getAttribute?.("href") || "");
      // Conversation links inside a project name the CHAT, not the project.
      if (/\/g\/g-p-[^/?#]+\/c\//i.test(href)) continue;
      const isProject = /\/g\/g-p-[^/?#]+\/(?:project)?(?:[?#]|$)/i.test(href)
        || /\/project(?:s)?\//i.test(href)
        || /project/i.test(String(el.getAttribute?.("data-testid") || ""))
        || Boolean(el.getAttribute?.("data-project-id"));
      if (!name || !isProject) continue;
      const match = href.match(/\/(?:g|project|projects)\/([^/?#]+)/i);
      const segment = match?.[1] || String(el.getAttribute?.("data-project-id") || "");
      // A `/g/g-<id>` link without the project marker is a custom GPT.
      if (/^g-(?!p-)/i.test(segment)) continue;
      const id = QwqcHeyTabbyChild.projectCoreId(segment);
      if (id && !seen.has(id)) seen.set(id, { id, segment, name:name.slice(0,160) });
    }
    const projects = Array.from(seen.values());
    for (const name of QwqcHeyTabbyChild.sidebarProjectNames(this.document)) {
      if (!projects.some(p => p.name.toLowerCase() === name.toLowerCase()))
        projects.push({ id:"", segment:"", name:name.slice(0,160) });
    }
    return { ok:true, result:"projects-discovered", projects, ...this.publicState() };
  }

  openProjectComposer(projectName) {
    const name=String(projectName||"").trim().toLocaleLowerCase();
    if(!name || name.length>90) return {ok:false,result:"invalid-project-name"};
    const rows=Array.from(this.document.querySelectorAll('button'))
      .filter(el=>String(el.getAttribute?.("aria-label")||"").trim().toLocaleLowerCase()
        ==="new chat in "+name && this.visible(el));
    if(rows.length!==1) return {ok:false,result:"project-compose-control-not-unique",candidates:rows.length};
    const previousUrl=String(this.contentWindow.location.href||"");
    const clicked=this.trustedClick(rows[0]);
    return {ok:clicked,result:clicked?"project-composer-requested":"project-composer-click-failed",
      previousUrl,name};
  }

  openSidebarProject(projectName) {
    const name=String(projectName||"").trim().toLocaleLowerCase();
    if(!name || name.length>90) return {ok:false,result:"invalid-project-name"};
    // Project entries are role=button rows. The adjacent buttons named
    // "Project actions for …" and "New chat in …" are NOT navigation.
    const rows=Array.from(this.document.querySelectorAll('[role="button"].sidebar-item'))
      .filter(el=>String(el.innerText||el.textContent||"").replace(/\s+/g," ").trim().toLocaleLowerCase()===name);
    if(rows.length!==1) return {ok:false,result:"project-sidebar-row-not-unique",candidates:rows.length};
    const before=String(this.contentWindow.location.href||"");
    const clicked=this.trustedClick(rows[0]);
    return {ok:clicked,result:clicked?"project-navigation-requested":"project-navigation-click-failed",
            previousUrl:before,name};
  }

  projectDiagnostics() {
    const doc=this.document;
    if (!doc) return {ok:false,result:"document-unavailable"};
    const elements=Array.from(doc.querySelectorAll(
      'a[href*="/g/"],a[href*="project"],[data-project-id],'
      +'nav a,nav button,[role="navigation"] a,[role="navigation"] button'
    )).slice(0,120);
    const projectButtons=Array.from(doc.querySelectorAll('button,[role="button"]'))
      .filter(el=>/project actions for|new chat in|move to project|conversation|sidebar/i.test(
        String(el.getAttribute?.("aria-label")||"") + " " +
        String(el.innerText||el.textContent||"").slice(0,60))).slice(0,60);
    const projectRows=projectButtons.map(el=>({
      aria:String(el.getAttribute?.("aria-label")||"").slice(0,110),
      visible:this.visible(el),
      ancestors:(()=>{
        let x=el;const result=[];
        for(let i=0;i<5 && x;i++,x=x.parentElement)
          result.push({
            tag:x.tagName,role:x.getAttribute?.("role")||"",
            href:String(x.getAttribute?.("href")||"").slice(0,160),
            className:String(x.getAttribute?.("class")||"").slice(0,165),
            dataId:String(x.getAttribute?.("data-project-id")||"").slice(0,100),
            text:String(x.innerText||x.textContent||"").replace(/\s+/g," ").trim().slice(0,125)
          });
        return result;
      })()
    }));
    const chatId=String(this.contentWindow.location.href||"").match(/\/c\/([0-9a-fA-F-]{36})(?:[?#]|$)/)?.[1] || "";
    const chatAnchors=chatId ? Array.from(doc.querySelectorAll('a[href*="/c/"]'))
      .filter(a=>String(a.getAttribute("href")||"").includes("/c/"+chatId)) : [];
    const chatRows=chatAnchors.slice(0,5).map(anchor=>{
      const ancestors=[];
      let node=anchor;
      for(let i=0;i<7 && node;i++,node=node.parentElement){
        ancestors.push({tag:node.tagName,role:node.getAttribute?.("role")||"",
          className:String(node.className||"").slice(0,130),
          buttons:Array.from(node.querySelectorAll('button')).slice(0,8).map(b=>({
            aria:b.getAttribute("aria-label")||"",
            width:b.getBoundingClientRect?.().width||0,
            visible:this.visible(b)
          }))});
      }
      return {href:anchor.getAttribute("href"),ancestors};
    });
    return {ok:true,result:"project-diagnostics",url:String(this.contentWindow.location.href),
      projectRows,chatRows,
      navigationCount:elements.length,entries:elements.map(el=>({
        tag:el.tagName,visible:this.visible(el),
        text:String(el.innerText||el.textContent||"").replace(/\s+/g," ").trim().slice(0,95),
        aria:String(el.getAttribute?.("aria-label")||"").slice(0,95),
        testid:String(el.getAttribute?.("data-testid")||"").slice(0,95),
        href:String(el.getAttribute?.("href")||"").slice(0,150),
        id:String(el.getAttribute?.("data-project-id")||"").slice(0,100)
      }))};
  }

  async waitForRoute(predicate, timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    for (;;) {
      const route = QwqcHeyTabbyChild.parseChatRoute(this.contentWindow.location.href);
      if (route && predicate(route)) return route;
      if (Date.now() >= deadline) return null;
      await new Promise(r => this.contentWindow.setTimeout(r, 200));
    }
  }

  async moveMenuDiagnostics(expectedConversationId) {
    const route = QwqcHeyTabbyChild.parseChatRoute(this.contentWindow.location.href);
    if (!route || route.conversationId !== expectedConversationId)
      return {ok:false,result:"diagnostic-conversation-mismatch"};
    const wait = ms => new Promise(resolve=>this.contentWindow.setTimeout(resolve,ms));
    const menu = () => Array.from(this.document.querySelectorAll(
      '[role="menu"],[role="menuitem"],[role="option"],[role="dialog"],button,[role="button"],input'))
      .filter(el=>this.visible(el))
      .filter(el=>el.closest?.('[role="menu"],[role="dialog"]') ||
        /move|project|working|rename/i.test(QwqcHeyTabbyChild.labelFor(el)))
      .slice(0,65).map(el=>({tag:el.tagName,role:el.getAttribute?.('role')||'',
        aria:el.getAttribute?.('aria-label')||'',text:String(el.innerText||el.textContent||'').replace(/\s+/g,' ').trim().slice(0,90),
        state:el.getAttribute?.('data-state')||'',html:String(el.outerHTML||'').slice(0,280)}));
    const links = Array.from(this.document.querySelectorAll('a[href*="/c/"]')).filter(a=>
      String(a.getAttribute('href')||'').includes('/c/'+expectedConversationId));
    if(links.length!==1)return {ok:false,result:'diagnostic-chat-row-not-unique',rows:links.length};
    let row=links[0],actions=null;
    for(let i=0;i<7&&row;i++,row=row.parentElement){
      const found=Array.from(row.querySelectorAll?.('button[aria-label="Chat actions"]')||[]);
      if(found.length===1){actions=found[0];break;}
    }
    if(!actions)return {ok:false,result:'diagnostic-chat-actions-not-found'};
    if(!this.trustedClick(actions))return {ok:false,result:'diagnostic-chat-actions-click-failed'};
    await wait(250);
    const afterChatActions=menu();
    const openerChoices=()=>Array.from(this.document.querySelectorAll('button,[role="button"],[role="menuitem"]'))
      .filter(el=>this.visible(el)&&/move to project|add to project/i.test(QwqcHeyTabbyChild.labelFor(el)));
    let choices=openerChoices(), afterDirect=[];
    if(choices.length!==1){
      try{actions.click()}catch(_){}
      await wait(300);
      afterDirect=menu();choices=openerChoices();
    }
    let afterPointer=[], pointerError='';
    if(choices.length!==1){
      try {
        const rect=actions.getBoundingClientRect();
        const EventType=this.contentWindow.PointerEvent;
        const event=new EventType('pointerdown',{bubbles:true,cancelable:true,pointerType:'mouse',
          pointerId:1,isPrimary:true,button:0,buttons:1,
          clientX:rect.left+rect.width/2,clientY:rect.top+rect.height/2});
        actions.dispatchEvent(event);
      } catch (error) {pointerError=String(error);}
      await wait(300);
      afterPointer=menu();choices=openerChoices();
    }
    if(choices.length!==1)return {ok:true,result:'diagnostic-chat-menu',openerCount:choices.length,
      actionHtml:String(actions.outerHTML||'').slice(0,700),
      rect:(()=>{const r=actions.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}})(),
      pointerError,afterChatActions,afterDirect,afterPointer};
    if(!this.trustedClick(choices[0]))return {ok:false,result:'diagnostic-move-opener-click-failed',afterChatActions,afterDirect};
    await wait(350);
    const afterMove=menu();
    try{choices[0].click()}catch(_){}
    await wait(300);
    return {ok:true,result:'diagnostic-move-menu',afterChatActions,afterDirect,afterPointer,afterMove,afterMoveDirect:menu()};
  }

  async moveToProject(projectId, projectName, expectedConversationId = "", routeTimeoutMs = 8000) {
    const wantedName = String(projectName || "").trim();
    const wanted = wantedName.toLowerCase();
    const wantedId = QwqcHeyTabbyChild.projectCoreId(projectId);
    if (!wantedName || !wantedId) return { ok:false, result:"missing-project-identity", ...this.publicState() };
    const before = QwqcHeyTabbyChild.parseChatRoute(this.contentWindow.location.href);
    if (!before) return { ok:false, result:"not-a-conversation", ...this.publicState() };
    const conversationId = String(expectedConversationId || before.conversationId);
    if (before.conversationId !== conversationId)
      return { ok:false, result:"conversation-mismatch", ...this.publicState() };
    const verifiedResult = (result, route) => ({ ok:true, result, projectId:wantedId,
      projectSegment:route.projectSegment, projectName:wantedName, conversationId, ...this.publicState() });
    // Moving is idempotent: never click through menus when the route already
    // proves the conversation lives in the requested project.
    if (before.projectId === wantedId) return verifiedResult("already-in-project", before);
    const wait = ms => new Promise(resolve => this.contentWindow.setTimeout(resolve, ms));
    const controls = () => Array.from(this.document.querySelectorAll(
      'button,[role="button"],[role="menuitem"],[role="option"]')).filter(el => this.visible(el));
    const labels = () => controls().filter(el => ["menuitem","option"].includes(el.getAttribute?.("role")))
      .slice(0,30).map(el => QwqcHeyTabbyChild.labelFor(el).slice(0,100));
    const findMove = () => controls().find(el =>
      /(move to project|add to project)/i.test(QwqcHeyTabbyChild.labelFor(el)));
    let opener = findMove();
    let exact = null;
    if (!opener) {
      // The canonical route appears before ChatGPT hydrates its controls.
      // Do not treat that first frame as a permanently missing project menu.
      // This is read-only polling: no unrelated chat or Voice UI is clicked.
      for (let attempt = 0; attempt < 8 && !opener; attempt++) {
        await wait(120);
        const current = QwqcHeyTabbyChild.parseChatRoute(this.contentWindow.location.href);
        if (!current || current.conversationId !== conversationId)
          return { ok:false, result:"conversation-changed-during-menu-wait", ...this.publicState() };
        opener = findMove();
      }
    }
    if (!opener) {
      // The current UI exposes Move to project from this chat's own sidebar
      // row ("Chat actions"), which is CSS-hidden until the row is hovered.
      const links = Array.from(this.document.querySelectorAll('a[href*="/c/"]'))
        .filter(a => String(a.getAttribute("href") || "").includes("/c/" + conversationId));
      for (const link of links) {
        let row = link;
        for (let i = 0; i < 7 && row; i++, row = row.parentElement) {
          const buttons = Array.from(row.querySelectorAll?.('button[aria-label="Chat actions"]') || []);
          if (buttons.length === 1) { exact = buttons[0]; break; }
        }
        if (exact) break;
      }
      if (exact) {
        // Hover first: the sidebar gives Chat actions pointer events only after
        // the row has activated hover styling on a subsequent animation frame.
        const r = exact.getBoundingClientRect();
        try { this.contentWindow.windowUtils.sendMouseEvent("mousemove", r.left + r.width / 2, r.top + r.height / 2, 0, 0, 0); } catch (_) {}
        await wait(180);
        this.radixPointerDown(exact);
      } else {
        const more = controls().find(el => /^(more|more actions|conversation options|open conversation options)$/i.test(
          QwqcHeyTabbyChild.labelFor(el)));
        if (more) this.trustedClick(more);
      }
      for (let i = 0; i < 8 && !opener; i++) { await wait(120); opener = findMove(); }
      if (!opener && exact) {
        // Synthetic Gecko pointer movement may be ignored by a CSS-hidden
        // button; call the exact Chat actions button as a final safe fallback.
        try { exact.click(); } catch (_) {}
        await wait(180);
        opener = findMove();
      }
    }
    if (!opener) return { ok:false, result:"move-project-control-not-found",
      chatActionFound:Boolean(exact), visibleMenuLabels:labels(), ...this.publicState() };
    // Radix root opens on pointerdown, but its nested submenu trigger opens
    // on the trusted Gecko mouse click (confirmed from the live Zen menu).
    if (!this.trustedClick(opener)) return { ok:false, result:"move-project-opener-click-failed", ...this.publicState() };
    await wait(180);
    const menuItems = () => controls().filter(el => ["menuitem","option"].includes(el.getAttribute?.("role")));
    let choices = [];
    for (let i = 0; i < 10 && !choices.length; i++) {
      choices = menuItems().filter(el => QwqcHeyTabbyChild.projectLabels(el).includes(wanted));
      if (!choices.length) await wait(120);
    }
    // Two menu entries with the same visible name cannot be told apart by the
    // UI, so refuse rather than risk moving the chat into the wrong project.
    if (choices.length > 1) return { ok:false, result:"project-choice-ambiguous", visibleMenuLabels:labels(), ...this.publicState() };
    if (!choices[0] || !this.selectRadixItem(choices[0]))
      return { ok:false, result:"project-choice-not-found", visibleMenuLabels:labels(), ...this.publicState() };
    const after = await this.waitForRoute(route =>
      route.conversationId === conversationId && route.projectId === wantedId, routeTimeoutMs);
    if (!after) return { ok:false, result:"project-move-unverified", projectId:"", projectName:"", conversationId, ...this.publicState() };
    return verifiedResult("project-move-verified", after);
  }

  async renameChat(expectedConversationId, desiredName) {
    // Rename only the exact chat owned by this worker window. Never use the
    // first visible Chat actions menu, which may belong to another chat.
    const name = String(desiredName || "").replace(/\s+/g, " ").trim().slice(0, 100);
    const route = () => QwqcHeyTabbyChild.parseChatRoute(this.contentWindow.location.href);
    if (!name || !expectedConversationId || route()?.conversationId !== expectedConversationId)
      return {ok:false,result:"rename-identity-invalid"};
    const links = () => Array.from(this.document.querySelectorAll('a[href*="/c/"]')).filter(a => {
      const href = String(a.getAttribute?.("href") || a.href || "");
      return /\/c\/([^/?#]+)/.exec(href)?.[1] === expectedConversationId;
    });
    const visible = el => this.visible(el);
    const linkName = el => String(el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
    let found = links();
    if (found.length !== 1) return {ok:false,result:found.length ? "rename-chat-link-ambiguous":"rename-chat-link-not-found"};
    if (linkName(found[0]) === name)
      return {ok:true,result:"chat-name-already-matches",name,conversationId:expectedConversationId};
    let node = found[0], action = null;
    for (let i=0; i<7 && node; i++,node=node.parentElement) {
      const controls = Array.from(node.querySelectorAll?.('button[aria-label="Chat actions"]') || []);
      if (controls.length === 1) {action=controls[0];break;}
    }
    if (!action) return {ok:false,result:"rename-chat-actions-not-found"};
    const safe = () => route()?.conversationId === expectedConversationId;
    const wait = ms => new Promise(resolve => this.contentWindow.setTimeout(resolve,ms));
    try {
      const rect = action.getBoundingClientRect?.();
      if (rect?.width > 0 && rect?.height > 0)
        this.contentWindow.windowUtils?.sendMouseEvent("mousemove",rect.left+rect.width/2,rect.top+rect.height/2,0,0,0);
    } catch (_) {}
    await wait(160);
    if (!safe() || !this.radixPointerDown(action)) return {ok:false,result:"rename-menu-open-unverified"};
    let choice = null;
    for (let attempt=0; attempt<12; attempt++) {
      const choices = Array.from(this.document.querySelectorAll('[role="menuitem"],button,[role="button"]'))
        .filter(el => visible(el) && QwqcHeyTabbyChild.projectLabels(el).some(v => /^(rename|rename chat)$/.test(v)));
      if (choices.length > 1) return {ok:false,result:"rename-choice-ambiguous"};
      if (choices.length === 1) {choice=choices[0]; break;}
      await wait(120);
      if (!safe()) return {ok:false,result:"rename-conversation-changed"};
    }
    if (!choice) return {ok:false,result:"rename-choice-not-found"};
    if (!this.selectRadixItem(choice) || !safe()) return {ok:false,result:"rename-editor-open-unverified"};
    let editor = null;
    for (let attempt=0;attempt<12;attempt++) {
      const candidates = Array.from(this.document.querySelectorAll(
        '[role="dialog"] input, input[aria-label*="rename" i],input[placeholder*="name" i],input[data-testid*="rename"],input[aria-label*="chat" i]'))
        .filter(el => visible(el) && !el.disabled);
      if (candidates.length > 1) return {ok:false,result:"rename-editor-ambiguous"};
      if (candidates.length === 1) {editor=candidates[0];break;}
      await wait(100);
      if (!safe()) return {ok:false,result:"rename-conversation-changed"};
    }
    if (!editor) return {ok:false,result:"rename-editor-not-found"};
    editor.focus?.();
    // React-controlled input: use the native setter, then a bubbling input.
    const prototype = this.contentWindow.HTMLInputElement?.prototype;
    const setter = prototype && Object.getOwnPropertyDescriptor(prototype,"value")?.set;
    if (setter) setter.call(editor,name);
    else editor.value=name;
    editor.dispatchEvent?.(new this.contentWindow.Event("input",{bubbles:true}));
    if (!safe()) return {ok:false,result:"rename-conversation-changed"};
    const save = Array.from(this.document.querySelectorAll('[role="dialog"] button,button,[role="button"]'))
      .filter(el => visible(el) && QwqcHeyTabbyChild.projectLabels(el).some(v => /^(save|rename|confirm)$/.test(v)));
    if (save.length > 1) return {ok:false,result:"rename-save-ambiguous"};
    if (save.length === 1) {
      if (!this.trustedClick(save[0])) return {ok:false,result:"rename-save-click-failed"};
    } else {
      editor.dispatchEvent?.(new this.contentWindow.KeyboardEvent("keydown",{key:"Enter",code:"Enter",bubbles:true}));
    }
    for (let attempt=0;attempt<20;attempt++) {
      if (!safe()) return {ok:false,result:"rename-conversation-changed"};
      found=links();
      if (found.length===1 && linkName(found[0])===name)
        return {ok:true,result:"chat-name-verified",name,conversationId:expectedConversationId};
      await wait(140);
    }
    return {ok:false,result:"chat-name-unverified"};
  }

  userTurns() {
    const doc = this.document;
    const semantic = Array.from(doc?.querySelectorAll?.('[data-message-author-role="user"]') || []);
    if (semantic.length) {
      const last = semantic[semantic.length - 1];
      return { count: semantic.length, lastText: String(last.innerText || last.textContent || "").trim() };
    }
    // Virtualised renderer fallback: each user turn carries a screen-reader
    // "You said:" heading.
    const body = String(doc?.body?.innerText || "");
    const chunks = body.split("You said:");
    const lastChunk = chunks.length > 1 ? chunks[chunks.length - 1] : "";
    return { count: chunks.length - 1, lastText: lastChunk.split("ChatGPT said:")[0].trim() };
  }

  originSnapshot() {
    // Read-only evidence from THIS tab. Never supplies a browser-wide "current"
    // alias: the controller must match it to the invoking request uniquely.
    const route = QwqcHeyTabbyChild.parseChatRoute(this.contentWindow.location.href);
    if (!route) return { ok:false, result:"not-a-conversation" };
    const elements = Array.from(this.document.querySelectorAll('[data-message-author-role="user"]'));
    let userMessages = elements.map(el => String(el.innerText || el.textContent || "").replace(/\s+/g, " ").trim())
      .filter(Boolean).slice(-3);
    if (!userMessages.length) {
      const chunks = String(this.document.body?.innerText || "").split("You said:").slice(1);
      userMessages = chunks.map(chunk => chunk.split("ChatGPT said:")[0].replace(/\s+/g, " ").trim())
        .filter(Boolean).slice(-3);
    }
    return { ok:true, result:"origin-snapshot", href:String(this.contentWindow.location.href),
      conversationId:route.conversationId, userMessages };
  }

  conversationTurns() {
    const users = this.userTurns();
    const assistant = this.latestAssistantResponse();
    const state = this.state();
    return { ok:true, result:"conversation-turns", userCount:users.count,
      lastUserText:users.lastText.slice(0, 4000), assistantCount:assistant.assistantCount || 0,
      route:QwqcHeyTabbyChild.parseChatRoute(this.contentWindow.location.href), ...this.publicState(state) };
  }

  // The route check, composer fill and click happen in one actor call so no
  // navigation can slip in between verifying the project and sending. Every
  // failure before the click reports sent:false; after the click the outcome
  // is either confirmed or explicitly "unknown" so callers never resend blind.
  async sendPromptGuarded(data) {
    const conversationId = String(data?.conversationId || "");
    const wantedId = QwqcHeyTabbyChild.projectCoreId(data?.projectId);
    const text = String(data?.text ?? "");
    const notSent = result => ({ ok:false, sent:false, result, ...this.publicState() });
    if (!conversationId || !wantedId || !text.trim()) return notSent("missing-send-identity");
    const routeOk = () => {
      const route = QwqcHeyTabbyChild.parseChatRoute(this.contentWindow.location.href);
      return Boolean(route && route.conversationId === conversationId && route.projectId === wantedId);
    };
    if (!routeOk()) return notSent("route-mismatch");
    const idleDeadline = Date.now() + Math.max(0, Number(data?.idleTimeoutMs ?? 20000));
    while (this.state().working) {
      if (Date.now() >= idleDeadline) return notSent("assistant-still-working");
      await new Promise(r => this.contentWindow.setTimeout(r, 250));
    }
    const composer = await this.waitForComposer(6000);
    if (!composer) return notSent("composer-not-found");
    const before = this.userTurns().count;
    // The caller records how many user turns existed after the bootstrap. Any
    // other count means a prompt may already be in the chat: refuse to send.
    const expected = Number(data?.expectedUserCount ?? -1);
    if (expected >= 0 && before !== expected) return { ...notSent("user-count-mismatch"), userCount:before };
    this.setComposerText(composer, text);
    let send = null;
    const deadline = Date.now() + 6000;
    while (Date.now() < deadline) {
      send = this.findSendButton();
      if (send && !(send.disabled || send.getAttribute?.("aria-disabled") === "true")) break;
      send = null;
      await new Promise(r => this.contentWindow.setTimeout(r, 100));
    }
    if (!send) { this.setComposerText(composer, ""); return notSent("send-button-not-found"); }
    // Last-moment re-check: the route must still be the verified project.
    if (!routeOk()) { this.setComposerText(composer, ""); return notSent("route-mismatch"); }
    if (!this.trustedClick(send)) return notSent("send-click-failed");
    const confirmDeadline = Date.now() + Math.max(500, Number(data?.confirmTimeoutMs ?? 8000));
    while (Date.now() < confirmDeadline) {
      await new Promise(r => this.contentWindow.setTimeout(r, 200));
      if (this.userTurns().count > before || this.state().working)
        return { ok:true, sent:true, result:"prompt-sent", userCountBefore:before, ...this.publicState() };
    }
    return { ok:false, sent:"unknown", result:"prompt-send-unconfirmed", userCountBefore:before, ...this.publicState() };
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
      case "promptSubmissionState": return this.promptSubmissionState(message.data?.text ?? "");
      case "openProjectComposer": return this.openProjectComposer(message.data?.name);
      case "openSidebarProject": return this.openSidebarProject(message.data?.name);
      case "moveMenuDiagnostics": return this.moveMenuDiagnostics(message.data?.conversationId);
      case "projectDiagnostics": return this.projectDiagnostics();
      case "discoverProjects": return this.discoverProjects();
      case "moveToProject": return this.moveToProject(message.data?.projectId, message.data?.projectName, message.data?.conversationId);
      case "renameChat": return this.renameChat(message.data?.conversationId,message.data?.name);
      case "originSnapshot": return this.originSnapshot();
      case "conversationTurns": return this.conversationTurns();
      case "openProject": return this.openProject(message.data?.name);
      case "sendPromptGuarded": return this.sendPromptGuarded(message.data || {});
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
