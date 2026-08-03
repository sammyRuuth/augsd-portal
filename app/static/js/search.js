/**
 * AUGSD Portal - Search utilities
 */

const SearchUtils = {
    debounceTimeout: null,

    debounce(func, delay = 300) {
        return (...args) => {
            if (this.debounceTimeout) {
                clearTimeout(this.debounceTimeout);
            }
            this.debounceTimeout = setTimeout(() => {
                func.apply(this, args);
            }, delay);
        };
    },

    highlight(text, query) {
        if (!query) return text;
        const regex = new RegExp(`(${query})`, 'gi');
        return text.replace(regex, '<mark class="bg-yellow-200">$1</mark>');
    },

    normalizeQuery(query) {
        return query.toLowerCase().trim();
    },
};

// Export for use in other scripts
if (typeof module !== 'undefined' && module.exports) {
    module.exports = SearchUtils;
}
