(document.title.split(' | ').find(s => s.startsWith('#')) || location.pathname.split('/').pop()).replace(/^#/, '').replace(/[^\w.-]+/g, '-')
