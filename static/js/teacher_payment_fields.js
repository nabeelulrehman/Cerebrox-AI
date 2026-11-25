(() => {
  const initialize = (root = document) => {
    root.querySelectorAll('[data-teacher-payment-form]').forEach((form) => {
      const method = form.querySelector('[name="payment_method"]');
      const number = form.querySelector('[name="account_number"]');
      const title = form.querySelector('[name="account_title"]');
      const bank = form.querySelector('[name="bank_name"]');
      const iban = form.querySelector('[name="iban"]');
      if (!method || !number || !title || form.dataset.paymentFieldsReady) return;
      form.dataset.paymentFieldsReady = '1';

      const update = () => {
        const selected = method.value;
        const mobileWallet = selected === 'jazzcash' || selected === 'easypaisa';
        const bankTransfer = selected === 'bank_transfer';
        const setField = (field, visible, required, label) => {
          if (!field) return;
          const wrapper = field.closest('div');
          if (wrapper) wrapper.hidden = !visible;
          field.required = required;
          if (label) {
            const labelElement = form.querySelector(`label[for="${field.id}"]`);
            if (labelElement) labelElement.textContent = label;
          }
        };
        setField(title, !!selected, !!selected);
        setField(number, !!selected, !!selected, mobileWallet ? 'Mobile Number' : 'Account Number');
        setField(bank, bankTransfer, bankTransfer);
        setField(iban, bankTransfer, bankTransfer);
      };
      method.addEventListener('change', update);
      update();
    });
  };

  window.initializeTeacherPaymentFields = initialize;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => initialize());
  else initialize();
})();
