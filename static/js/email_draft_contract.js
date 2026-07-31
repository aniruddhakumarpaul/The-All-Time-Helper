// Shared browser-side email-draft normalization and safe boundary serializers.
(function () {
    const SCHEMA_VERSION = 1;
    const MIME_RE = /^[a-z0-9][a-z0-9!#$&^_.+-]*\/[a-z0-9][a-z0-9!#$&^_.+-]*$/i;

    function text(value, fallback = '') {
        return String(value ?? fallback).trim();
    }

    function filename(value, fallback = 'attachment.bin') {
        const clean = text(value, fallback).replace(/\\/g, '/').split('/').pop().replace(/^[ .]+|[ .]+$/g, '');
        return (clean || fallback).slice(0, 160);
    }

    function attachment(raw, index, legacyContent) {
        const item = raw && typeof raw === 'object' ? raw : { content: raw };
        const content = item.content ?? item.data ?? (index === 0 ? legacyContent : null);
        const id = text(item.id) || null;
        const mimeType = text(item.mime_type || item.content_type || item.type, 'application/octet-stream').toLowerCase();
        const available = item.available ?? Boolean(id || content);
        const source = ['upload', 'generated', 'legacy', 'remote', 'unknown'].includes(text(item.source))
            ? text(item.source) : (content && !id ? 'generated' : id ? 'upload' : 'unknown');
        const result = {
            id,
            filename: filename(item.filename || item.name, `attachment-${index + 1}.bin`),
            name: filename(item.filename || item.name, `attachment-${index + 1}.bin`),
            mime_type: MIME_RE.test(mimeType) ? mimeType : 'application/octet-stream',
            type: MIME_RE.test(mimeType) ? mimeType : 'application/octet-stream',
            content_type: MIME_RE.test(mimeType) ? mimeType : 'application/octet-stream',
            size: Number.isFinite(Number(item.size)) && Number(item.size) >= 0 ? Number(item.size) : undefined,
            sha256: /^[0-9a-f]{64}$/i.test(text(item.sha256 || item.sha_256)) ? text(item.sha256 || item.sha_256).toLowerCase() : undefined,
            available: available == null ? undefined : Boolean(available),
            source,
        };
        if (content != null) result.content = String(content);
        return result;
    }

    function normalize(raw) {
        if (!raw || typeof raw !== 'object') return null;
        const rawAttachments = Array.isArray(raw.attachments) ? raw.attachments : [];
        const legacyContent = raw.attachment_content ?? null;
        const attachments = rawAttachments.map((item, index) => attachment(item, index, legacyContent));
        if (!attachments.length && (legacyContent != null || raw.attachment_filename)) {
            attachments.push(attachment({
                content: legacyContent,
                filename: raw.attachment_filename,
                type: raw.attachment_type || raw.content_type || raw.type,
                source: 'legacy',
            }, 0, legacyContent));
        }
        if (attachments[0] && legacyContent != null && !attachments[0].content) attachments[0].content = String(legacyContent);
        const primary = attachments[0] || {};
        return {
            schema_version: SCHEMA_VERSION,
            recipient: text(raw.recipient || raw.to),
            subject: text(raw.subject),
            body: String(raw.body || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim(),
            tone: ['formal', 'informal', 'modern'].includes(text(raw.tone, 'modern')) ? text(raw.tone, 'modern') : 'modern',
            attachment_content: primary.content ?? null,
            attachment_filename: attachments.length ? primary.filename : '',
            attachment_type: attachments.length ? primary.mime_type : undefined,
            attachment_description: raw.attachment_description ? text(raw.attachment_description) : undefined,
            attachments,
            has_attachment_content: Boolean(primary.content || raw.has_attachment_content),
        };
    }

    function stripContent(item) {
        const next = { ...item };
        delete next.content;
        delete next.data;
        delete next.bytes;
        delete next.attachment_content;
        return next;
    }

    function serializeFullTransient(raw) {
        const draft = normalize(raw);
        if (!draft) return null;
        return { ...draft, attachments: draft.attachments.map(item => ({ ...item })) };
    }

    function serializePromptContext(raw) {
        const draft = normalize(raw);
        if (!draft) return null;
        const result = { ...draft, attachment_content: null, attachments: draft.attachments.map(stripContent) };
        delete result.has_attachment_content;
        return result;
    }

    function serializePersistable(raw) {
        const result = serializePromptContext(raw);
        if (!result) return null;
        delete result.attachment_content;
        delete result.attachment_filename;
        delete result.attachment_type;
        return result;
    }

    window.helperEmailDraftContract = {
        SCHEMA_VERSION,
        normalize,
        serializeFullTransient,
        serializePromptContext,
        serializePersistable,
    };
})();
