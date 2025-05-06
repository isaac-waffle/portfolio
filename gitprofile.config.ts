// gitprofile.config.ts

const CONFIG = {
  github: {
    username: 'isaac-waffle', // Your GitHub org/user name. (This is the only required config)
  },
  /**
   * If you are deploying to https://<USERNAME>.github.io/, for example your repository is at https://github.com/arifszn/arifszn.github.io, set base to '/'.
   * If you are deploying to https://<USERNAME>.github.io/<REPO_NAME>/,
   * for example your repository is at https://github.com/arifszn/portfolio, then set base to '/portfolio/'.
   */
  base: '/portfolio/',
  projects: {
    github: {
      display: false, // Display GitHub projects?
      header: 'Github Projects',
      mode: 'automatic', // Mode can be: 'automatic' or 'manual'
      automatic: {
        sortBy: 'stars', // Sort projects by 'stars' or 'updated'
        limit: 8, // How many projects to display.
        exclude: {
          forks: false, // Forked projects will not be displayed if set to true.
          projects: [], // These projects will not be displayed. example: ['arifszn/my-project1', 'arifszn/my-project2']
        },
      },
      manual: {
        // Properties for manually specifying projects
        projects: ['arifszn/gitprofile', 'arifszn/pandora'], // List of repository names to display. example: ['arifszn/my-project1', 'arifszn/my-project2']
      },
    },    
    external: {
      header: 'Projects',
      projects: [
        {
          title: 'Bank Churn',
          description:
            'Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed euismod, nunc ut.',
          imageUrl:
            'https://img.freepik.com/free-vector/illustration-gallery-icon_53876-27002.jpg',
          link: 'https://example.com',
        },
        {
          title: 'Project Name',
          description:
            'Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed euismod, nunc ut.',
          imageUrl:
            'https://img.freepik.com/free-vector/illustration-gallery-icon_53876-27002.jpg',
          link: 'https://example.com',
        },
      ],
    },
  },
  seo: {
    title: 'Portfolio of Isaac Kim',
    description: '',
    imageURL: '',
  },
  social: {
    linkedin: 'eunho-kim',
    website: 'https://www.arifszn.com',
    phone: '',
    email: 'eunho203@gmail.com',
  },
  skills: [
    'Python',
  ],
  experiences: [
    {
      company: 'Infineon Technologies',
      position: 'Data Scientist Intern',
      from: 'November 2024',
      to: 'April 2025',
      companyLink: 'https://www.infineon.com/',
    },
  ],
  certifications: [
    {
      name: 'Lorem ipsum',
      body: 'Lorem ipsum dolor sit amet',
      year: 'March 2022',
      link: 'https://example.com',
    },
  ],
  educations: [
    {
    institution: 'University of Amsterdam',
    degree: "Bachelor of Science",
    major: 'Econometrics and Data Science',
    from: '2019',
    to: '2024',
    honors: 'Cum Laude',
    },
    {
      institution: 'University of Ajou',
      degree: "Bachelor of Science",
      major: 'Industrial Engineering',  
      from: '2018',
      to: '2018',
    },
  ],
  publications: [
    {
      title: 'A Comparative Analysis of Explainable AI Techniques and Econometrics',
      authors: 'Isaac Kim',
      link: 'https://scripties.uba.uva.nl/search?id=c11249314',
      description:
        'This paper compares traditional econometric models with machine learning methods using Explainable AI techniques like ALE. ALE reveals complex, non-linear patterns and provides interpretable effect size metrics. The study shows XAI improves understanding of black-box models.',
    },
  ],
  // Display articles from your medium or dev account. (Optional)
  blog: {
  source: '', // or 'medium' / 'dev'
  username: '',
  limit: 0,
  },
  googleAnalytics: {
    id: 'G-LF9100HZBE', // GA3 tracking id/GA4 tag id UA-XXXXXXXXX-X | G-XXXXXXXXXX
  },
  // Track visitor interaction and behavior. https://www.hotjar.com
  hotjar: {
    id: '',
    snippetVersion: 6,
  },
  themeConfig: {
    defaultTheme: 'light',

    // Hides the switch in the navbar
    // Useful if you want to support a single color mode
    disableSwitch: true,

    // Should use the prefers-color-scheme media-query,
    // using user system preferences, instead of the hardcoded defaultTheme
    respectPrefersColorScheme: false,

    // Display the ring in Profile picture
    displayAvatarRing: true,

    // Available themes. To remove any theme, exclude from here.
    themes: [
      'light',
      'dark',
      'cupcake',
      'bumblebee',
      'emerald',
      'corporate',
      'synthwave',
      'retro',
      'cyberpunk',
      'valentine',
      'halloween',
      'garden',
      'forest',
      'aqua',
      'lofi',
      'pastel',
      'fantasy',
      'wireframe',
      'black',
      'luxury',
      'dracula',
      'cmyk',
      'autumn',
      'business',
      'acid',
      'lemonade',
      'night',
      'coffee',
      'winter',
      'dim',
      'nord',
      'sunset',
      'procyon',
    ],

    // Custom theme, applied to `procyon` theme
    customTheme: {
      primary: '#fc055b',
      secondary: '#219aaf',
      accent: '#e8d03a',
      neutral: '#2A2730',
      'base-100': '#E3E3ED',
      '--rounded-box': '3rem',
      '--rounded-btn': '3rem',
    },
  },

  enablePWA: true,
};

export default CONFIG;
