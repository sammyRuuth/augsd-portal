/**
 * AUGSD Portal - Calendar utilities
 */

const CalendarUtils = {
    DAYS: ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'],
    START_HOUR: 8,
    END_HOUR: 19,
    COLORS: [
        { bg: '#dbeafe', border: '#2563eb' },
        { bg: '#dcfce7', border: '#16a34a' },
        { bg: '#fef3c7', border: '#d97706' },
        { bg: '#ede9fe', border: '#7c3aed' },
        { bg: '#fce7f3', border: '#db2777' },
        { bg: '#ccfbf1', border: '#0d9488' },
        { bg: '#fee2e2', border: '#dc2626' },
    ],

    formatTime(timeStr) {
        if (!timeStr) return 'TBA';
        const [hours, minutes] = timeStr.split(':');
        const h = parseInt(hours);
        const ampm = h >= 12 ? 'PM' : 'AM';
        const h12 = h % 12 || 12;
        return `${h12}:${minutes} ${ampm}`;
    },

    parseTime(timeStr) {
        if (!timeStr) return null;
        const [hours, minutes] = timeStr.split(':').map(Number);
        return { hours, minutes };
    },

    getColor(index) {
        return this.COLORS[index % this.COLORS.length];
    },
};

// Export for use in other scripts
if (typeof module !== 'undefined' && module.exports) {
    module.exports = CalendarUtils;
}
