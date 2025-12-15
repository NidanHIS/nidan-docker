window.config = {
  routerBasename: '/ohif',
  showStudyList: true,
  servers: {
    dicomWeb: [
      {
        name: 'Orthanc',
        wadoUriRoot: '/orthanc-container/wado',
        qidoRoot: '/orthanc-container/dicom-web',
        wadoRoot: '/orthanc-container/dicom-web',
        qidoSupportsIncludeField: true,
        imageRendering: 'wadors',
        thumbnailRendering: 'wadors',
      },
    ],
  },
  hotkeys: [],
};

