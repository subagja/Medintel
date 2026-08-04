"use strict";

document.addEventListener("DOMContentLoaded", () => {
    const reliabilityField = document.querySelector(
        "#id_source_reliability"
    );
    const credibilityField = document.querySelector(
        "#id_information_credibility"
    );
    const codePreview = document.querySelector(
        "#admiralty-code-preview"
    );
    const descriptionPreview = document.querySelector(
        "#admiralty-code-description"
    );

    const searchInput = document.querySelector(
        "#article-search"
    );
    const statusFilter = document.querySelector(
        "#article-status-filter"
    );
    const articleItems = document.querySelectorAll(
        ".medintel-article-item"
    );

    const reliabilityDescriptions = {
        A: "Sumber sepenuhnya dapat dipercaya",
        B: "Sumber biasanya dapat dipercaya",
        C: "Sumber cukup dapat dipercaya",
        D: "Sumber biasanya tidak dapat dipercaya",
        E: "Sumber tidak dapat dipercaya",
        F: "Reliabilitas sumber belum dapat dinilai",
    };

    const credibilityDescriptions = {
        1: "informasi telah dikonfirmasi oleh sumber lain",
        2: "informasi kemungkinan besar benar",
        3: "informasi mungkin benar",
        4: "informasi diragukan",
        5: "informasi kemungkinan tidak benar",
        6: "kebenaran informasi belum dapat dinilai",
    };

    function updateAdmiraltyCode() {
        if (
            !reliabilityField
            || !credibilityField
            || !codePreview
        ) {
            return;
        }

        const reliability = reliabilityField.value;
        const credibility = credibilityField.value;

        codePreview.textContent =
            `${reliability}${credibility}`;

        if (descriptionPreview) {
            descriptionPreview.textContent =
                `${reliabilityDescriptions[reliability]} dan ` +
                `${credibilityDescriptions[credibility]}.`;
        }
    }

    function filterArticles() {
        if (!searchInput || !statusFilter) {
            return;
        }

        const keyword = searchInput.value
            .toLowerCase()
            .trim();

        const selectedStatus = statusFilter.value;

        articleItems.forEach((item) => {
            const title = item.dataset.title || "";
            const source = item.dataset.source || "";
            const status = item.dataset.status || "pending";

            const matchesKeyword =
                !keyword
                || title.includes(keyword)
                || source.includes(keyword);

            const matchesStatus =
                !selectedStatus
                || status === selectedStatus;

            item.classList.toggle(
                "d-none",
                !(matchesKeyword && matchesStatus)
            );
        });
    }

    reliabilityField?.addEventListener(
        "change",
        updateAdmiraltyCode
    );

    credibilityField?.addEventListener(
        "change",
        updateAdmiraltyCode
    );

    searchInput?.addEventListener(
        "input",
        filterArticles
    );

    statusFilter?.addEventListener(
        "change",
        filterArticles
    );

    updateAdmiraltyCode();
});