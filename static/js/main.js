document.addEventListener('DOMContentLoaded', function() {
    // Initialize Lucide Icons
    if (typeof lucide !== 'undefined') {
        lucide.createIcons();
    }

    // Navbar Scroll Effect
    window.onscroll = function() {
        const nav = document.querySelector('nav');
        if (nav) {
            if (window.scrollY > 50) {
                nav.classList.add('nav-scrolled', 'bg-[#FAF9F6]/85', 'backdrop-blur-md', 'border-b', 'border-pink-100/50', 'shadow-sm', 'py-3');
                nav.classList.remove('bg-transparent', 'py-4');
            } else {
                nav.classList.remove('nav-scrolled', 'bg-[#FAF9F6]/85', 'backdrop-blur-md', 'border-b', 'border-pink-100/50', 'shadow-sm', 'py-3');
                nav.classList.add('bg-transparent', 'py-4');
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
    const modal = document.getElementById('staffModal');
    const content = document.getElementById('modal-content');
    
    if (modal) {
        modal.classList.remove('hidden');
        document.body.style.overflow = 'hidden'; // Prevent scrolling when modal is open
        
        // Fetch staff details via AJAX
        fetch(`/employee/${id}/`)
            .then(response => {
                if (!response.ok) throw new Error('Network response was not ok');
                return response.text();
            })
            .then(html => {
                if (content) {
                    content.innerHTML = html;
                }
            })
            .catch(error => {
                console.error('Error loading staff details:', error);
            });
    }
}

function closeStaffModal() {
    const modal = document.getElementById('staffModal');
    if (modal) {
        modal.classList.add('hidden');
        document.body.style.overflow = 'auto';
    }
}

// Close modal on click outside
window.onclick = function(event) {
    const modal = document.getElementById('staff-modal');
    if (event.target == modal) {
        closeStaffModal();
    }
}
