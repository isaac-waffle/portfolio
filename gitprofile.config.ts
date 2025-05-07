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
          title: 'Bank Churn Prediction',
          description:
            'Neural Network, Random Forest.',
          imageUrl:
            'https://img.freepik.com/free-vector/illustration-gallery-icon_53876-27002.jpg',
          link: 'https://example.com',
        },
        {
          title: 'Data Science Blog',
          description:
            'Machine Learning, Explainable AI(XAI)',
          imageUrl:
            'https://img.freepik.com/free-vector/illustration-gallery-icon_53876-27002.jpg',
          link: 'https://isaac-waffle.github.io/',
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
    website: '',
    phone: '',
    email: 'eunho203@gmail.com',
  },
  skills: [
    'Python', 'R', 'SQL', 'Data Analyst', 'Automation', 'Big Data Management',
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
      name: 'SAS Certified Specialist',
      body: 'Base Programming Using SAS 9.4 ',
      year: 'Aug 2024',
      link: '',
    },
    {
      name: 'Data Visualization and Communication with Tableau',
      body: 'Duke University',
      year: 'Aug 2024',
      link: 'https://www.coursera.org/account/accomplishments/certificate/SLLYQ0B54LX5',
    },
    {
      name: 'SAS Programmer',
      body: 'SAS',
      year: 'Aug 2024',
      link: 'https://www.coursera.org/account/accomplishments/specialization/certificate/CNYXRG9WIEA4',
    },  
    {
      name: 'Databases and SQL for Data Science with Python',
      body: 'with honors, IBM',
      year: 'JUL 2024',
      link: 'https://www.coursera.org/account/accomplishments/certificate/SN6JCJCMASDN',
    },
    {
      name: 'Exam P',
      body: 'Society of Actuaries',
      year: 'Jul 2020',
      link: '',
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
        'This paper compares traditional econometric models with machine learning methods using Explainable AI techniques like ALE, showing XAI improves understanding of black-box models.',
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
