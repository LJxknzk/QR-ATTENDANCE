(function(){
    // Shared server config for mobile web assets.
    window.BASE_URL = (typeof SERVER_URL !== 'undefined' && SERVER_URL) ? SERVER_URL.replace(/\/$/, '') : '';
    try {
        const params = new URLSearchParams(window.location.search);
        const s = params.get('server');
        if (!window.BASE_URL && s) window.BASE_URL = s.replace(/\/$/, '');
    } catch (e) {}

    if (!window.BASE_URL && window.location && window.location.protocol === 'file:') {
        window.BASE_URL = localStorage.getItem('SERVER_URL') || 'http://localhost:5000';
    }

    window.api = function(path) {
        if (!path.startsWith('/')) path = '/' + path;
        return window.BASE_URL ? window.BASE_URL.replace(/\/$/, '') + path : path;
    };
})();
