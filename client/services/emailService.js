// This function POSTs feedback to the Flask backend at `/send-feedback`.
const BACKEND = import.meta.env.VITE_BACKEND_URL || "http://127.0.0.1:5001";

export const sendFeedbackEmail = async (formData) => {
    try {
        const res = await fetch(`${BACKEND}/send-feedback`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: formData.name || 'Anonymous',
                email: formData.email || 'no-reply@example.com',
                subject: formData.subject || 'Feedback - Rural Healthcare System',
                message: formData.message || 'No message',
                // Optional 1-5 star rating -- omitted (undefined) when the
                // user didn't pick one, which JSON.stringify simply drops.
                rating: formData.rating || undefined
            })
        });

        const body = await res.json().catch(() => ({}));
        if (res.ok && body.success) {
            return { success: true, message: body.message || 'Feedback sent successfully!' };
        }

        return { success: false, message: body.error || body.message || 'Failed to send feedback.' };
    } catch (err) {
        console.error('Feedback send error:', err);
        return { success: false, message: 'Could not send feedback. Please try again later.' };
    }
};
