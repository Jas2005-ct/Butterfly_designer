document.addEventListener('DOMContentLoaded', function() {
    // Initialize Lucide Icons
    if (typeof lucide !== 'undefined') {
        lucide.createIcons();
    }

    // Scroll Progress Bar
    window.onscroll = function() {
        let winScroll = document.body.scrollTop || document.documentElement.scrollTop;
        let height = document.documentElement.scrollHeight - document.documentElement.clientHeight;
        let scrolled = (winScroll / height) * 100;
        let progressBar = document.getElementById("progress-bar");
        if (progressBar) {
            progressBar.style.width = scrolled + "%";
        }
        
        // Navbar Scroll Effect
        const nav = document.querySelector('nav');
        if (nav) {
            if (window.scrollY > 50) {
                nav.classList.add('nav-scrolled');
            } else {
                nav.classList.remove('nav-scrolled');
            }
        }
    };

    // Scroll Reveal Animation
    const observerOptions = {
        threshold: 0.1
    };

    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('active');
            }
        });
    }, observerOptions);

    document.querySelectorAll('section, header > div, .reveal-item').forEach(el => {
        el.classList.add('reveal');
        observer.observe(el);
    });
});

// Staff Modal Logic
function openStaffModal(id) {
    const modal = document.getElementById('staff-modal');
    const content = document.getElementById('modal-content');
    
    modal.classList.remove('hidden');
    modal.classList.add('flex');
    
    // Fetch staff details via AJAX
    fetch(`/employee/${id}/`)
        .then(response => response.text())
        .then(html => {
            content.innerHTML = html;
        });
}

function closeStaffModal() {
    const modal = document.getElementById('staff-modal');
    modal.classList.add('hidden');
    modal.classList.remove('flex');
}

// Close modal on click outside
window.onclick = function(event) {
    const modal = document.getElementById('staff-modal');
    if (event.target == modal) {
        closeStaffModal();
    }
}
