document.addEventListener('DOMContentLoaded', () => {
  const filtersForm = document.getElementById('catalogFilters');
  if (!filtersForm) {
    return;
  }

  const langInput = document.getElementById('langInput');
  const pageInput = document.getElementById('pageInput');
  const langButtons = filtersForm.querySelectorAll('.lang-btn');
  const autoSubmitElements = filtersForm.querySelectorAll('[data-auto-submit]');
  const submitButton = document.getElementById('filterSubmit');

  const currentLang = (langInput && langInput.value) ? langInput.value : 'en';
  const storedLang = localStorage.getItem('nobleLang') || currentLang;

  const activateLangButton = (lang) => {
    langButtons.forEach((btn) => {
      btn.classList.toggle('is-active', btn.dataset.lang === lang);
    });
  };

  const setLanguage = (lang, submitAfterChange = false) => {
    if (!langInput) {
      return;
    }
    langInput.value = lang;
    localStorage.setItem('nobleLang', lang);
    activateLangButton(lang);

    if (submitAfterChange && filtersForm) {
      if (pageInput) {
        pageInput.value = 1;
      }
      filtersForm.submit();
    }
  };

  // Align UI with stored language preference
  if (storedLang !== currentLang) {
    setLanguage(storedLang, true);
    return; // form submit will reload page with correct language
  } else {
    setLanguage(currentLang);
  }

  langButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
      const lang = btn.dataset.lang;
      if (!lang || (langInput && lang === langInput.value)) {
        return;
      }
      setLanguage(lang, true);
    });
  });

  autoSubmitElements.forEach((element) => {
    element.addEventListener('change', () => {
      if (pageInput) {
        pageInput.value = 1;
      }
      filtersForm.submit();
    });
  });

  if (submitButton) {
    submitButton.addEventListener('click', () => {
      if (pageInput) {
        pageInput.value = 1;
      }
    });
  }

  filtersForm.addEventListener('submit', () => {
    if (langInput && !langInput.value) {
      langInput.value = 'en';
    }
  });
});
