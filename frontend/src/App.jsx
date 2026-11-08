import React, { useState } from 'react';
import StaffDashboard from './components/StaffDashboard';
import ClinicianView from './components/ClinicianView';

export default function App() {
  const [activeTab, setActiveTab] = useState('staff');

  return (
    <div className="min-h-screen bg-gray-100 text-gray-800">
      <header className="bg-white border-b border-gray-200 sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-6 py-4 flex justify-between items-center">
          <span className="font-bold text-xl text-blue-700">Outpatient Scheduler MVP</span>
          <nav className="flex space-x-2">
            <button
              onClick={() => setActiveTab('staff')}
              className={`px-4 py-2 rounded-md text-sm font-medium transition ${
                activeTab === 'staff' ? 'bg-blue-600 text-white' : 'text-gray-600 hover:bg-gray-100'
              }`}
            >
              Front-Desk Staff
            </button>
            <button
              onClick={() => setActiveTab('clinician')}
              className={`px-4 py-2 rounded-md text-sm font-medium transition ${
                activeTab === 'clinician' ? 'bg-blue-600 text-white' : 'text-gray-600 hover:bg-gray-100'
              }`}
            >
              Clinician View
            </button>
          </nav>
        </div>
      </header>

      <main className="py-6">
        {activeTab === 'staff' && <StaffDashboard />}
        {activeTab === 'clinician' && <ClinicianView clinicianId="1" />}
      </main>
    </div>
  );
}
