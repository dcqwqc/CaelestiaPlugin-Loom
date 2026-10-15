// Transient task-event projection. Never changes durable tasks or other alerts.
function visibleTaskNotices(notifications, tasks, now) {
    const notices = Array.isArray(notifications) ? notifications : [];
    const statusByTask = Object.create(null);
    const newestByTask = Object.create(null);
    const clock = Number(now);
    if (Array.isArray(tasks)) {
        for (const task of tasks) {
            if (task && task.id)
                statusByTask[String(task.id)] = task;
        }
    }
    function eligible(notice) {
        if (!notice || notice.status === 'expired' || notice.status === 'dismissed')
            return false;
        if (notice.kind !== 'status' ||
                !String(notice.request_key || '').startsWith('agent-event-'))
            return true;
        const created = Number(notice.created_at);
        const expiry = notice.expires_at == null ? created + 900 : Number(notice.expires_at);
        if (!Number.isFinite(clock) || !Number.isFinite(created) ||
                !Number.isFinite(expiry) || created <= 0 || expiry <= clock)
            return false;
        const id = String(notice.task_id || '');
        if (!id) return true; // Legacy task notices also expire by age.
        const state = String(notice.task_status || '');
        const current = statusByTask[id];
        if (current === undefined)
            return state === 'done'; // A completed task may be purged.
        if (String(current.status || '') !== state) return false;
        // A recent callback does not validate an old task's Working state.
        if (state === 'working') {
            const updated = Number(current.updatedAt || 0);
            if (!Number.isFinite(updated) || updated <= 0 ||
                    clock - updated >= 900) return false;
        }
        return true;
    }
    // Pick the latest eligible update for each stable task ID, regardless of order.
    for (const notice of notices) {
        if (!eligible(notice) || !notice.task_id) continue;
        const id = String(notice.task_id);
        const previous = newestByTask[id];
        if (!previous || Number(notice.created_at) > Number(previous.created_at) ||
                (Number(notice.created_at) === Number(previous.created_at) &&
                 String(notice.id) > String(previous.id)))
            newestByTask[id] = notice;
    }
    return notices.filter(notice => eligible(notice) &&
        (!notice.task_id || newestByTask[String(notice.task_id)] === notice));
}
