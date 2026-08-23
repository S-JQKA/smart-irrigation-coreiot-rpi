let injector = widgetContext.$scope.$injector;
let attributeService = injector.get(
    widgetContext.servicesMap.get('attributeService')
);

let defaultStart = new Date(Date.now() + 2 * 60 * 1000);
defaultStart.setMinutes(
    defaultStart.getMinutes() - defaultStart.getTimezoneOffset()
);
let startText = window.prompt(
    'Start time (YYYY-MM-DDTHH:mm)',
    defaultStart.toISOString().slice(0, 16)
);
if (!startText) return;

let durationText = window.prompt('Duration in seconds', '60');
if (!durationText) return;

let repeatText = window.prompt(
    'Repeat every seconds; 0 = run once',
    '0'
);
if (repeatText === null) return;

let startAtMs = new Date(startText).getTime();
let durationSeconds = Number(durationText);
let repeatEverySeconds = Number(repeatText);

if (
    !Number.isFinite(startAtMs) ||
    startAtMs <= Date.now() ||
    startAtMs > Date.now() + 7 * 24 * 60 * 60 * 1000 ||
    !Number.isInteger(durationSeconds) ||
    durationSeconds < 1 ||
    !Number.isInteger(repeatEverySeconds) ||
    repeatEverySeconds < 0 ||
    (repeatEverySeconds > 0 && repeatEverySeconds < durationSeconds)
) {
    window.alert(
        'Invalid input: start must be within 7 days; duration >= 1; ' +
        'repeat must be 0 or >= duration.'
    );
    return;
}

let now = Date.now();
let safeEntityName = String(entityName || 'smart-valve')
    .replace(/\s+/g, '-')
    .toLowerCase();
let config = {
    schemaVersion: 1,
    configId: 'dashboard-schedule-' + safeEntityName + '-' + now,
    scheduleId: safeEntityName + '-operator',
    enabled: true,
    startAtMs: startAtMs,
    durationSeconds: durationSeconds,
    repeatEverySeconds: repeatEverySeconds
};

attributeService.saveEntityAttributes(
    entityId,
    'SHARED_SCOPE',
    [{key: 'localScheduleConfig', value: config}]
).subscribe(
    function() {
        console.log('localScheduleConfig saved', config);
        window.alert('Schedule saved; wait for Gateway Config ACK');
        widgetContext.updateAliases();
    },
    function(error) {
        console.warn('localScheduleConfig save failed', error);
        window.alert(
            'Attribute save failed: ' +
            (error.message || JSON.stringify(error))
        );
    }
);
